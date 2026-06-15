package com.jarvis.android.system.llm

import android.app.ActivityManager
import android.content.Context
import android.util.Log
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.DelicateCoroutinesApi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.GlobalScope
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.float
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import java.io.File
import javax.inject.Inject
import javax.inject.Singleton

/**
 * [LocalLlmBackend] backed by llama.cpp via the native [jarvis_llm] JNI library.
 *
 * Supports any GGUF-quantized model (Gemma 4, Llama 3, Phi-4, Mistral, Qwen, …).
 * GPU acceleration uses Vulkan via ggml-vulkan (configured in CMakeLists.txt).
 *
 * The native library is compiled from `cpp/llama_bridge.cpp` and requires the
 * `llama.cpp` git submodule to be present. If the `.so` is absent the init
 * call logs a warning and [isLoaded] stays false so IntelliRouter falls back
 * to another backend gracefully.
 *
 * Thread model:
 *   [generate] blocks the calling thread during inference (llama.cpp is
 *   single-threaded per context). The [Flow] runs on [Dispatchers.IO] so
 *   the UI is never blocked. Each emitted String is a UTF-8 token piece.
 */
@Singleton
class LlamaJNI @Inject constructor(
    @ApplicationContext private val context: Context,
) : LocalLlmBackend {

    override val backendId = "llamacpp"

    private var sessionHandle: Long = INVALID_HANDLE
    private var loadedConfig: LlmLoadConfig? = null
    private var cachedInfo: LlmInfo = LlmInfo(
        backendId   = backendId,
        modelName   = "None",
        paramCount  = "—",
        quantFormat = "GGUF",
        sizeMb      = 0f,
        contextLen  = 0,
    )

    override val isLoaded: Boolean
        get() = sessionHandle != INVALID_HANDLE

    // ── Library load ─────────────────────────────────────────────────────────

    companion object {
        const val TAG           = "LlamaJNI"
        const val INVALID_HANDLE = -1L

        // Smallest legitimate GGUF in our catalog is Qwen 0.5B at ~397 MB, so
        // anything under 1 MB is unambiguously truncated — header-only error
        // pages, aborted first-chunk downloads, or size-zero placeholders.
        private const val MIN_GGUF_BYTES = 1_000_000L

        val isNativeAvailable: Boolean by lazy {
            try {
                System.loadLibrary("jarvis_llm")
                nativeInit()
                Log.i(TAG, "jarvis_llm native library loaded")
                true
            } catch (e: UnsatisfiedLinkError) {
                Log.w(TAG, "jarvis_llm not available — llama.cpp submodule not compiled: ${e.message}")
                false
            }
        }

        // ── JNI declarations ─────────────────────────────────────────────

        @JvmStatic external fun nativeInit()
        @JvmStatic external fun nativeLoadModel(
            modelPath:   String,
            nGpuLayers:  Int,
            contextSize: Int,
        ): Long

        @JvmStatic external fun nativeRunInference(
            handle:      Long,
            prompt:      String,
            maxTokens:   Int,
            callback:    TokenCallback,
        )

        @JvmStatic external fun nativeStopInference(handle: Long)
        @JvmStatic external fun nativeGetModelInfo(handle: Long): String
        @JvmStatic external fun nativeUnloadModel(handle: Long)
        @JvmStatic external fun nativeDestroy()

        /**
         * Format a prompt using the loaded model's own chat template (read from
         * the GGUF's `tokenizer.chat_template` metadata). Returns an empty
         * string if the model has no template or the native call fails — the
         * caller should fall back to a default format in that case.
         */
        @JvmStatic external fun nativeApplyChatTemplate(
            handle:       Long,
            systemPrompt: String,
            userPrompt:   String,
        ): String
    }

    // ── TokenCallback (called from C++ for each generated piece) ─────────────

    /**
     * Implemented inline per-inference as a [callbackFlow] send lambda.
     * The C++ side calls [onToken] synchronously on the inference thread.
     * Return `true` to continue generation, `false` to stop early.
     */
    fun interface TokenCallback {
        fun onToken(piece: String): Boolean
    }

    // ── LocalLlmBackend impl ──────────────────────────────────────────────────

    override suspend fun load(config: LlmLoadConfig) {
        if (isLoaded && loadedConfig == config) {
            Log.d(TAG, "Already loaded: ${config.modelPath}")
            return
        }
        if (isLoaded) unload()

        if (!isNativeAvailable) {
            // The GGUF/llama.cpp backend requires the llama.cpp submodule to
            // be cloned and compiled at build time. That adds ~500 MB of C++
            // to the build and pushes the APK size up substantially, so most
            // debug builds ship without it. MediaPipe-backed models (Gemma 2
            // 2B and Gemma 3 1B, ~530 MB–1.3 GB) work out of the box and are
            // the recommended on-device path — this error points users there
            // instead of leaving them guessing.
            throw RuntimeException(
                "GGUF models need llama.cpp, which isn't bundled in this build. " +
                "Try 'Gemma 3 1B (on-device)' — it runs via MediaPipe and works immediately."
            )
        }

        val file = File(config.modelPath)
        if (!file.exists()) {
            throw IllegalStateException("Model file not found: ${config.modelPath}")
        }

        // Pre-flight validation. The downloader can promote a tmp file to final
        // without a completeness check — a truncated or zero-padded GGUF then
        // SIGSEGVs deep inside llama_model_load_from_file with no recoverable
        // log line. Validate magic + minimum size here so we surface a clear,
        // actionable error instead of crashing the process.
        validateGgufOrThrow(file)

        // Memory-aware context clamp. The catalog often declares the model's
        // native context (e.g. 16384 for CodeLlama 7B) but a 7B model with
        // 16k context needs ~4 GB just for the KV cache on top of 4 GB of
        // weights. On a phone with 11 GB total / 5 GB free that triggers
        // Android's low-memory killer mid-inference — the process dies
        // silently with no logcat entry, the UI looks stuck, and the user
        // sees "no answer". Cap the context so KV + weights + 1 GB system
        // headroom fits the actually-available memory at load time.
        val clampedCtx = clampContextForMemory(file, config.contextSize)
        val effectiveConfig = if (clampedCtx == config.contextSize) config
                              else config.copy(contextSize = clampedCtx)

        Log.i(TAG, "Loading GGUF model: ${file.name} (gpu=${effectiveConfig.nGpuLayers}, ctx=${effectiveConfig.contextSize}, size=${file.length()})")

        val handle = withContext(Dispatchers.IO) {
            nativeLoadModel(effectiveConfig.modelPath, effectiveConfig.nGpuLayers, effectiveConfig.contextSize)
        }

        if (handle == INVALID_HANDLE) {
            throw RuntimeException("llama.cpp failed to load model: ${config.modelPath}")
        }

        sessionHandle = handle
        loadedConfig  = effectiveConfig
        cachedInfo    = parseModelInfo(nativeGetModelInfo(handle), effectiveConfig)
        Log.i(TAG, "Model loaded: ${cachedInfo.modelName} (${cachedInfo.paramCount}, ${cachedInfo.sizeMb} MB)")
    }

    override fun generate(prompt: String, config: GenerationConfig): Flow<String> {
        check(isLoaded) { "No model loaded. Call load() first." }
        val handle = sessionHandle

        return callbackFlow {
            // The TokenCallback bridge: C++ calls this on the inference thread.
            // [trySend] is non-blocking and safe from any thread.
            val cb = TokenCallback { piece ->
                val result = trySend(piece)
                // If the channel is closed (collector cancelled), stop inference.
                result.isSuccess
            }

            // Build the prompt using the loaded model's own chat template
            // (read from its GGUF metadata via llama_chat_apply_template).
            // Every model family has different control tokens — baking
            // <start_of_turn> into the prompt for a Qwen or CodeLlama model
            // was feeding them garbage tokens and producing incoherent
            // replies on any non-Gemma model. If the native call returns
            // empty (model has no template metadata or call failed), fall
            // back to a minimal "System: ... User: ... Assistant: " format
            // that most chat-tuned models understand well enough.
            val templated = try {
                nativeApplyChatTemplate(handle, config.systemPrompt, prompt)
            } catch (t: Throwable) {
                Log.w(TAG, "nativeApplyChatTemplate threw, falling back: ${t.message}")
                ""
            }
            val fullPrompt = if (templated.isNotEmpty()) {
                templated
            } else {
                buildString {
                    if (config.systemPrompt.isNotBlank()) {
                        appendLine("System: ${config.systemPrompt}")
                    }
                    appendLine("User: $prompt")
                    append("Assistant:")
                }
            }

            // Run inference on IO — blocks until complete or stopped
            @OptIn(DelicateCoroutinesApi::class)
            val inferenceJob = GlobalScope.launch(Dispatchers.IO) {
                try {
                    nativeRunInference(handle, fullPrompt, config.maxNewTokens, cb)
                } catch (e: Exception) {
                    Log.e(TAG, "Inference error", e)
                    close(e)
                }
                close()  // signal end of stream
            }

            // When the collector cancels, stop native inference and join
            awaitClose {
                nativeStopInference(handle)
                inferenceJob.cancel()
            }
        }.flowOn(Dispatchers.IO)
    }

    override fun stop() {
        if (isLoaded) nativeStopInference(sessionHandle)
    }

    override suspend fun unload() {
        val handle = sessionHandle
        if (handle == INVALID_HANDLE) return
        withContext(Dispatchers.IO) {
            nativeUnloadModel(handle)
        }
        sessionHandle = INVALID_HANDLE
        loadedConfig  = null
        Log.i(TAG, "Model unloaded")
    }

    override fun info(): LlmInfo = cachedInfo

    // ── Helpers ───────────────────────────────────────────────────────────────

    private fun parseModelInfo(json: String, config: LlmLoadConfig): LlmInfo {
        return try {
            val obj        = Json.parseToJsonElement(json).jsonObject
            val name       = obj["name"]?.jsonPrimitive?.content ?: File(config.modelPath).nameWithoutExtension
            val params     = obj["params"]?.jsonPrimitive?.content ?: "?"
            val sizeMb     = obj["size_mb"]?.jsonPrimitive?.float ?: 0f
            val contextLen = obj["context_len"]?.jsonPrimitive?.int ?: config.contextSize
            LlmInfo(
                backendId   = backendId,
                modelName   = name,
                paramCount  = params,
                quantFormat = inferQuant(config.modelPath),
                sizeMb      = sizeMb,
                contextLen  = contextLen,
            )
        } catch (e: Exception) {
            LlmInfo(
                backendId   = backendId,
                modelName   = File(config.modelPath).nameWithoutExtension,
                paramCount  = "?",
                quantFormat = inferQuant(config.modelPath),
                sizeMb      = File(config.modelPath).length() / 1_048_576f,
                contextLen  = config.contextSize,
            )
        }
    }

    /**
     * Cap the requested context window so (weights + KV + compute + system
     * headroom) fits the memory actually available at load time.
     *
     * The catalog's contextLength is the model's native max, not a claim that
     * every device can run it. CodeLlama 7B's native 16384 would need ~4 GB
     * of KV even with Q8_0 quantization on top of 4 GB of weights — 8 GB
     * before any inference overhead. On a 12-GB phone with 5 GB free after
     * system + other apps, that's an immediate LMK kill on the first batch.
     *
     * Heuristic:
     *   kv_bytes_per_token ≈ weights_bytes / 15_000
     *     (empirical fit on llama-family Q4_K_M weights with Q8_0 KV; 7B/3.9GB
     *      → 260 KB/token matches the observed 262 KB/token from ggml logs)
     *   budget_for_kv = availMem - weights - 1 GB (system + compute + buffers)
     *   max_ctx       = budget_for_kv / kv_bytes_per_token
     *
     * Clamp to [512, requested]. If the device is so memory-constrained that
     * the max is below 512 we let the native loader fail naturally — a load
     * with <512 context is useless anyway.
     */
    private fun clampContextForMemory(file: File, requested: Int): Int {
        val am = context.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        val mi = ActivityManager.MemoryInfo().also { am.getMemoryInfo(it) }

        val availBytes   = mi.availMem
        val weightsBytes = file.length()
        val headroom     = 1_024L * 1_024 * 1_024     // 1 GB for system + compute + activations
        val budgetForKv  = (availBytes - weightsBytes - headroom).coerceAtLeast(0L)

        // Floor at 32 KB/tok so tiny-model loads don't divide-by-tiny and end
        // up with a silly 500k context the native loader won't actually try
        // to allocate.
        val kvPerToken   = (weightsBytes / 15_000L).coerceAtLeast(32_768L)
        val maxCtx       = (budgetForKv / kvPerToken).toInt().coerceAtLeast(512)
        val useCtx       = requested.coerceAtMost(maxCtx).coerceAtLeast(512)

        Log.i(TAG, "Memory-aware clamp: avail=${availBytes / 1_048_576}MB, " +
                   "weights=${weightsBytes / 1_048_576}MB, " +
                   "kvBudget=${budgetForKv / 1_048_576}MB, " +
                   "kv/tok=${kvPerToken}B, " +
                   "requestedCtx=$requested, maxCtx=$maxCtx, usingCtx=$useCtx")

        if (useCtx < requested) {
            Log.w(TAG, "Clamping context from $requested to $useCtx to avoid low-memory-killer")
        }
        return useCtx
    }

    /**
     * Reject truncated, zero-padded, or non-GGUF files before handing them to
     * llama.cpp. Every legitimate GGUF starts with the ASCII magic "GGUF"; a
     * failed download typically ends up as either an HTML error page (magic
     * "<!DO"), a zero-padded blob from parallel-range holes (magic "\0\0\0\0"),
     * or a much-smaller-than-expected partial file. Any of those would crash
     * the native loader — stopping here lets us tell the user what to do.
     */
    private fun validateGgufOrThrow(file: File) {
        val size = file.length()
        if (size < MIN_GGUF_BYTES) {
            throw IllegalStateException(
                "Model file is too small (${size} bytes) — likely a truncated " +
                "download. Delete and re-download from the Models screen."
            )
        }
        val magic = ByteArray(4)
        val read = file.inputStream().use { it.read(magic) }
        if (read != 4) {
            throw IllegalStateException(
                "Model file could not be read (got $read bytes of header). " +
                "The file is corrupt — delete and re-download."
            )
        }
        val magicStr = String(magic, Charsets.US_ASCII)
        if (magicStr != "GGUF") {
            throw IllegalStateException(
                "Model file is not a valid GGUF (header='${magicStr}', expected 'GGUF'). " +
                "The download likely captured an error page or was truncated. " +
                "Delete and re-download."
            )
        }
    }

    /** Extract quantisation suffix from filename, e.g. "gemma-4b-Q4_K_M.gguf" → "Q4_K_M". */
    private fun inferQuant(path: String): String {
        val name = File(path).nameWithoutExtension.uppercase()
        val quantPatterns = listOf("Q8_0", "Q6_K", "Q5_K_M", "Q5_K_S", "Q5_0",
                                   "Q4_K_M", "Q4_K_S", "Q4_0", "Q3_K_M", "Q2_K",
                                   "F16", "F32", "IQ4_XS", "IQ3_XXS")
        return quantPatterns.firstOrNull { name.contains(it) } ?: "GGUF"
    }
}
