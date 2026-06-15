package com.jarvis.android.system.llm

import android.content.Context
import android.util.Log
import com.google.ai.edge.litertlm.Backend
import com.google.ai.edge.litertlm.Content
import com.google.ai.edge.litertlm.Contents
import com.google.ai.edge.litertlm.Conversation
import com.google.ai.edge.litertlm.ConversationConfig
import com.google.ai.edge.litertlm.Engine
import com.google.ai.edge.litertlm.EngineConfig
import com.google.ai.edge.litertlm.Message
import com.google.ai.edge.litertlm.MessageCallback
import com.google.ai.edge.litertlm.SamplerConfig
import com.google.ai.edge.litertlm.ToolProvider
import com.google.ai.edge.litertlm.tool
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.withContext
import java.io.File
import java.util.concurrent.CancellationException
import java.util.concurrent.atomic.AtomicBoolean
import javax.inject.Inject
import javax.inject.Singleton

/**
 * [LocalLlmBackend] backed by Google AI Edge's LiteRT-LM runtime
 * (`com.google.ai.edge.litertlm:litertlm-android`).
 *
 * This is the replacement for the legacy MediaPipe Tasks GenAI path. LiteRT-LM
 * is what Google's own AI Edge Gallery uses and exposes real CPU / GPU (OpenCL)
 * / NPU (Hexagon on Snapdragon) / TPU backends via a single Kotlin API. Models
 * ship in a `.litertlm` bundle from the `litert-community` and `google`
 * HuggingFace organisations — the same models the AI Edge Gallery ships with.
 *
 * Lifecycle mirrors [LlamaJNI] / [MediaPipeLLM]:
 *   load() → generate() → unload()
 *
 * Acceleration:
 *   - GPU (OpenCL) is tried first — typically 3–10× the CPU throughput on
 *     Adreno / Mali chips. If the platform returns no OpenCL driver (emulator,
 *     old vendor image), Engine.initialize() throws and we retry on CPU.
 *   - NPU (Hexagon on Snapdragon 8 Gen 3+, S24/S25/S26) gives the best
 *     performance but the model must be quantised for it. For now we accept
 *     the GPU default — NPU can be enabled per-model later via config.
 */
@Singleton
class LiteRtLmBackend @Inject constructor(
    @ApplicationContext private val context: Context,
    private val jarvisTools: JarvisLiteRtTools,
) : LocalLlmBackend {

    override val backendId = "litertlm"

    @Volatile private var engine:       Engine?       = null
    @Volatile private var conversation: Conversation? = null
    private var loadedConfig: LlmLoadConfig? = null
    private var cachedInfo:   LlmInfo = defaultInfo()

    // Tracks what the current Conversation was built with, so generate() can
    // re-create it cheaply when the user swaps system prompt or sampler
    // settings between turns (a no-op if nothing changed). Null before the
    // first generate() call — Conversation is built lazily.
    @Volatile private var currentSystemPrompt: String = ""
    @Volatile private var currentSampler: SamplerTriple? = null

    // Function-calling / tool declarations to register at Conversation creation.
    // Left empty for now — wiring [com.jarvis.android.system.tools.JarvisToolDispatcher]
    // to LiteRT-LM's [ToolProvider] interface is a follow-up pass (Gemma 4 and
    // DeepSeek-R1 are the main models that emit structured tool calls). When
    // populated, tools are forwarded to every Conversation this backend creates.
    @Volatile private var tools: List<ToolProvider> = emptyList()

    private val cancelled = AtomicBoolean(false)

    override val isLoaded: Boolean
        get() = engine != null

    /**
     * Replace the tool list for subsequent generate() calls. Next generate()
     * will rebuild the Conversation so the new tools take effect.
     */
    fun setTools(newTools: List<ToolProvider>) {
        tools = newTools
        // Force conversation recreation on next generate().
        conversation = null
    }

    // ── Load ──────────────────────────────────────────────────────────────────

    override suspend fun load(config: LlmLoadConfig) {
        if (isLoaded && loadedConfig == config) return
        if (isLoaded) unload()

        val file = File(config.modelPath)
        if (!file.exists()) {
            throw IllegalStateException("LiteRT-LM model not found: ${config.modelPath}")
        }

        // Memory context logging. Previously we hard-refused the load when
        // the catalog's minDeviceMemoryInGb couldn't be met, but Google's
        // AI Edge Gallery runs the same models on the same phones by using
        // backends we weren't trying (NPU first on Snapdragon, which has
        // dedicated tensor memory). Log the numbers and let the engine
        // attempt the load on a real accelerator — a genuine allocation
        // failure still throws from Engine.initialize() and we surface
        // that to the user in the catch block below.
        val am = context.getSystemService(Context.ACTIVITY_SERVICE) as android.app.ActivityManager
        val memInfo = android.app.ActivityManager.MemoryInfo().also { am.getMemoryInfo(it) }
        val modelBytes = file.length()

        Log.i(TAG, "Loading LiteRT-LM model: ${file.name} " +
                   "(size=${modelBytes / 1_048_576}MB, " +
                   "availMem=${memInfo.availMem / 1_048_576}MB / " +
                   "${memInfo.totalMem / 1_048_576}MB)")

        withContext(Dispatchers.IO) {
            // Pick the engine backend — user-chosen via the per-model
            // ModelConfig dialog if set, else auto (GPU → CPU fallback).
            //
            // Google's AI Edge Gallery only exposes GPU / CPU in its config
            // for current Gemma / Qwen / DeepSeek `.litertlm` bundles — none
            // are compiled for the Hexagon NPU — so those are the two real
            // choices we surface. Forcing CPU is useful when GPU drivers
            // are flaky or the user wants deterministic behaviour; forcing
            // GPU skips the CPU fallback (errors surface instead of silently
            // degrading).
            val eng = when (config.accelerator?.uppercase()) {
                "CPU" -> {
                    Log.i(TAG, "Forced CPU backend (from ModelConfig)")
                    newEngine(config, Backend.CPU())
                }
                "GPU" -> {
                    Log.i(TAG, "Forced GPU backend (from ModelConfig)")
                    newEngine(config, Backend.GPU())
                }
                else -> runCatching {
                    Log.i(TAG, "Auto backend: trying GPU")
                    newEngine(config, Backend.GPU())
                }.getOrElse { gpuErr ->
                    Log.w(TAG, "GPU unavailable (${gpuErr.message}) — falling back to CPU")
                    newEngine(config, Backend.CPU())
                }
            }

            engine = eng
            // Don't pre-create a Conversation — we don't know the system
            // prompt or sampler settings until the first generate() call.
            // Building the Conversation lazily there lets us pick up
            // jarvis_persona.txt (threaded through GenerationConfig) and
            // any per-turn topK / topP / temperature overrides from the UI.
            conversation        = null
            currentSystemPrompt = ""
            currentSampler      = null
            // Register jarvis's tool adapter so Gemma 4 / Gemma 3n / DeepSeek
            // can invoke device tools (read_file, list_directory, get_system_info
            // for now — more follow). The top-level `tool()` helper wraps the
            // annotation-based [ToolSet] into the [ToolProvider] that the
            // ConversationConfig expects.
            tools = listOf(tool(jarvisTools))
        }

        loadedConfig = config
        cachedInfo   = LlmInfo(
            backendId   = backendId,
            modelName   = file.nameWithoutExtension,
            paramCount  = inferParamCount(file.name),
            quantFormat = "LiteRT-LM",
            sizeMb      = file.length() / 1_048_576f,
            contextLen  = config.contextSize,
        )
        Log.i(TAG, "LiteRT-LM loaded: ${cachedInfo.modelName} (${cachedInfo.paramCount}, ${cachedInfo.sizeMb} MB)")
    }

    private fun newEngine(config: LlmLoadConfig, backend: Backend): Engine {
        // Vision + audio backends match Google AI Edge Gallery's defaults —
        // see their model_allowlists entries. Gemma 3n / Gemma 4 multimodal
        // builds REQUIRE visionBackend to be specified (GPU) and audioBackend
        // on CPU; omitting them causes LiteRT-LM to either refuse to load
        // or silently fall back to a CPU-everywhere path that OOMs on the
        // main RAM pool. Non-multimodal models ignore these.
        val cfg = EngineConfig(
            modelPath     = config.modelPath,
            backend       = backend,
            visionBackend = Backend.GPU(),
            audioBackend  = Backend.CPU(),
            maxNumTokens  = config.contextSize.coerceIn(512, 32_000),
            // LiteRT-LM writes JIT-compiled kernels / cached graphs into this
            // dir. Use the app's external files dir so it survives restarts
            // and respects Android 11+ scoped storage.
            cacheDir      = context.getExternalFilesDir(null)?.absolutePath,
        )
        val eng = Engine(cfg)
        eng.initialize()
        Log.i(TAG, "Engine initialised on backend=${backend::class.simpleName}")
        return eng
    }

    // ── Generate ──────────────────────────────────────────────────────────────

    override fun generate(prompt: String, config: GenerationConfig): Flow<String> {
        val eng = checkNotNull(engine) { "LiteRT-LM model not loaded. Call load() first." }
        cancelled.set(false)

        // Extract the last user turn + effective system prompt from whatever
        // the caller gave us. ChatRepositoryImpl.buildLocalPrompt packs the
        // whole history in Qwen-flavoured ChatML:
        //   <|im_start|>system\n...<|im_end|>
        //   <|im_start|>user\n...<|im_end|>
        //   <|im_start|>assistant\n...<|im_end|>
        //   <|im_start|>user\n<last turn>
        //   <|im_start|>assistant\n
        // LiteRT-LM manages its own history + applies the model's native
        // template, so sending that wrapped string verbatim would double-wrap
        // the system + every turn. Peel the ChatML off, hand LiteRT-LM the
        // raw last user message, and set the system prompt via the
        // ConversationConfig below.
        val parsed        = parseChatMlOrRaw(prompt, fallbackSystem = config.systemPrompt)
        val systemPrompt  = parsed.system.ifBlank { config.systemPrompt }
        val userText      = parsed.userText
        val sampler       = SamplerTriple(
            topK        = config.topK,
            topP        = config.topP,
            temperature = config.temperature,
        )

        // Build (or reuse) a Conversation matching the current system prompt
        // + sampler. Recreating is cheap when nothing changed — the branch
        // below short-circuits in the steady state where the UI uses the
        // same persona and sampler across turns.
        val conv = ensureConversation(eng, systemPrompt, sampler)

        return callbackFlow<String> {
            val callback = object : MessageCallback {
                override fun onMessage(message: Message) {
                    // Message is a Kotlin data class — `toString()` dumps the
                    // whole structure (role, contents, toolCalls, channels)
                    // as JSON-ish text. Instead, pull only the Content.Text
                    // chunks from Contents and emit their raw string payload.
                    //
                    // Thinking-mode traces live in message.channels["thought"]
                    // for Gemma 4 and DeepSeek R1. When the user's saved
                    // ModelConfig has enableThinking=true (toggle in the
                    // Configurations dialog), we prepend the trace wrapped
                    // in <think>...</think> markers the UI can render as a
                    // collapsible block. Otherwise we drop the trace and
                    // stream only the final answer.
                    val piece = buildString {
                        if (config.enableThinking) {
                            message.channels["thought"]?.takeIf { it.isNotBlank() }?.let {
                                append("<think>")
                                append(it)
                                append("</think>\n")
                            }
                        }
                        message.contents.contents.forEach { part ->
                            if (part is Content.Text) append(part.text)
                        }
                    }
                    if (piece.isNotEmpty()) trySend(piece)
                }

                override fun onDone() {
                    close()
                }

                override fun onError(throwable: Throwable) {
                    if (throwable is CancellationException) {
                        close()
                    } else {
                        Log.e(TAG, "LiteRT-LM inference error", throwable)
                        close(throwable)
                    }
                }
            }

            // Assemble the multimodal message: images first (so the model sees
            // "here's a picture, now answer my question"), audio next, text
            // last. Gemma 4 / Gemma 3n accept PNG or JPEG as raw bytes; the
            // user's upload is already decoded in ChatRepositoryImpl. If the
            // model doesn't support image/audio input, LiteRT-LM drops those
            // parts gracefully and the Conversation just sees the text.
            val parts = buildList {
                config.images.forEach { add(Content.ImageBytes(it)) }
                config.audioClips.forEach { add(Content.AudioBytes(it)) }
                if (userText.isNotEmpty()) add(Content.Text(userText))
            }
            if (parts.isEmpty()) {
                // Defensive — an empty message would fail silently inside the
                // runtime. Close the flow with a clear error instead.
                close(IllegalArgumentException("LiteRT-LM: empty prompt (no text, images, or audio)"))
                return@callbackFlow
            }

            // Send the user prompt. The system instruction + tools were set at
            // createConversation time and are prepended automatically by the
            // LiteRT-LM runtime.
            conv.sendMessageAsync(
                Contents.of(parts),
                callback,
                /* extraContext = */ emptyMap(),
            )

            awaitClose {
                cancelled.set(true)
                runCatching { conv.cancelProcess() }
            }
        }.flowOn(Dispatchers.IO)
    }

    /**
     * Build the [Conversation] lazily. Only rebuilds when the system prompt or
     * sampler actually changes — the steady-state cost is a map lookup on the
     * hot path.
     */
    @Synchronized
    private fun ensureConversation(
        eng:          Engine,
        systemPrompt: String,
        sampler:      SamplerTriple,
    ): Conversation {
        val existing = conversation
        if (existing != null &&
            systemPrompt == currentSystemPrompt &&
            sampler      == currentSampler) {
            return existing
        }
        // Close the previous one if we had it; LiteRT-LM reclaims the KV
        // cache and kernel state when the conversation goes away.
        runCatching { existing?.close() }

        val fresh = eng.createConversation(
            ConversationConfig(
                samplerConfig = SamplerConfig(
                    topK        = sampler.topK,
                    topP        = sampler.topP.toDouble(),
                    temperature = sampler.temperature.toDouble(),
                ),
                systemInstruction = if (systemPrompt.isNotBlank()) {
                    Contents.of(listOf(Content.Text(systemPrompt)))
                } else null,
                tools = tools,
            )
        )
        conversation         = fresh
        currentSystemPrompt  = systemPrompt
        currentSampler       = sampler
        Log.i(TAG, "Conversation (re)built: sys=${systemPrompt.length} chars, " +
                   "sampler=$sampler, tools=${tools.size}")
        return fresh
    }

    override fun stop() {
        cancelled.set(true)
        runCatching { conversation?.cancelProcess() }
    }

    // ── Unload ────────────────────────────────────────────────────────────────

    override suspend fun unload() {
        withContext(Dispatchers.IO) {
            runCatching { conversation?.close() }
                .onFailure { Log.w(TAG, "conversation.close() failed: ${it.message}") }
            // Engine has no explicit shutdown in the 0.10.0 API surface; the
            // runtime releases native resources when the instance is GC'd.
        }
        conversation         = null
        engine               = null
        loadedConfig         = null
        currentSystemPrompt  = ""
        currentSampler       = null
        Log.i(TAG, "LiteRT-LM unloaded")
    }

    override fun info(): LlmInfo = cachedInfo

    // ── Helpers ───────────────────────────────────────────────────────────────

    /** Best-effort param count from filename: "gemma-4-E2B-it.litertlm" → "2B". */
    private fun inferParamCount(filename: String): String {
        val lower = filename.lowercase()
        val patterns = listOf(
            "270m" to "270M",
            "e2b" to "2B", "e4b" to "4B",
            "1.5b" to "1.5B", "1b" to "1B",
            "2b" to "2B", "3b" to "3B", "4b" to "4B",
            "7b" to "7B", "8b" to "8B", "9b" to "9B",
        )
        patterns.forEach { (needle, label) -> if (needle in lower) return label }
        return "?"
    }

    /**
     * Pull the system prompt and the LAST user turn out of a pre-ChatMLed
     * string. ChatML looks like `<|im_start|>user\n...<|im_end|>`. If no
     * ChatML markers are found we just treat the whole input as the raw
     * user message (what a caller that hasn't been adapted to LiteRT-LM yet
     * might send).
     */
    private data class ParsedChat(val system: String, val userText: String)

    private fun parseChatMlOrRaw(full: String, fallbackSystem: String): ParsedChat {
        if (!full.contains("<|im_start|>")) {
            return ParsedChat(system = fallbackSystem, userText = full)
        }
        val sysRegex  = Regex(
            """<\|im_start\|>system\n(.*?)<\|im_end\|>""",
            setOf(RegexOption.DOT_MATCHES_ALL),
        )
        val userRegex = Regex(
            """<\|im_start\|>user\n(.*?)(?:<\|im_end\|>|<\|im_start\|>|$)""",
            setOf(RegexOption.DOT_MATCHES_ALL),
        )
        val sys  = sysRegex.find(full)?.groupValues?.getOrNull(1)?.trim().orEmpty()
        val last = userRegex.findAll(full).lastOrNull()
            ?.groupValues?.getOrNull(1)?.trim().orEmpty()
        return ParsedChat(
            system   = if (sys.isNotBlank()) sys else fallbackSystem,
            userText = last.ifBlank { full },
        )
    }

    /**
     * Sampler hyperparams captured as a single value object so [ensureConversation]
     * can decide whether to rebuild the [Conversation] with a simple equality check.
     */
    private data class SamplerTriple(
        val topK:        Int,
        val topP:        Float,
        val temperature: Float,
    )

    private fun defaultInfo() = LlmInfo(
        backendId   = backendId,
        modelName   = "None",
        paramCount  = "—",
        quantFormat = "LiteRT-LM",
        sizeMb      = 0f,
        contextLen  = 0,
    )

    companion object {
        private const val TAG = "LiteRtLmBackend"

        // Defaults taken from Google AI Edge Gallery's model_allowlists:
        //   gemma-4 / gemma-3n: topK=64, topP=0.95, temperature=1.0
        //   qwen / deepseek / gemma3-1b: same defaults
        // Overriding per-model is a future extension via LlmLoadConfig.
        private const val DEFAULT_TOP_K       = 64
        private const val DEFAULT_TOP_P       = 0.95f
        private const val DEFAULT_TEMPERATURE = 1.0f
    }
}
