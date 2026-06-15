package com.jarvis.android.system.llm

import kotlinx.coroutines.flow.Flow

// ── Common config types ───────────────────────────────────────────────────────

/**
 * Parameters for loading a model into a backend.
 *
 * @param modelPath      Absolute path to the model file (.gguf / .litertlm / .task)
 * @param nGpuLayers     llama.cpp: transformer layers to GPU-offload. 0 = CPU only, -1 = all.
 * @param contextSize    KV-cache window in tokens.
 * @param nThreads       CPU inference threads (llama.cpp only).
 */
data class LlmLoadConfig(
    val modelPath:   String  = "",
    val nGpuLayers:  Int     = 0,
    val contextSize: Int     = 2048,
    val nThreads:    Int     = 4,
    /**
     * Which accelerator the LiteRT-LM engine should prefer. Null = auto
     * (try GPU → fall back to CPU). "GPU" or "CPU" force that choice,
     * mirroring Google AI Edge Gallery's per-model Accelerator picker.
     */
    val accelerator: String? = null,
)

/**
 * Per-generation sampling parameters.
 *
 * [images] and [audioClips] are optional multimodal inputs — only backends
 * whose model actually supports them (currently LiteRT-LM with Gemma 4 /
 * Gemma 3n) will pass them to the runtime. Text-only backends (llama.cpp,
 * Qwen via LiteRT-LM) silently ignore these so callers can always
 * attach multimedia without branching by backend.
 */
data class GenerationConfig(
    val maxNewTokens:    Int            = 512,
    val temperature:     Float          = 0.8f,
    val topK:            Int            = 40,
    val topP:            Float          = 0.95f,
    val repeatPenalty:   Float          = 1.1f,
    val systemPrompt:    String         = "",
    val images:          List<ByteArray> = emptyList(),
    val audioClips:      List<ByteArray> = emptyList(),
    /**
     * When true, preserve Gemma-4 / DeepSeek-R1 thinking-channel traces in
     * the streamed output so the UI can render them (e.g. as a collapsible
     * "Thinking" block). Off by default — most users want just the answer.
     */
    val enableThinking:  Boolean        = false,
)

/**
 * Snapshot of metadata about a loaded model.
 */
data class LlmInfo(
    val backendId:   String,
    val modelName:   String,
    val paramCount:  String,       // e.g. "7B"
    val quantFormat: String,       // e.g. "Q4_K_M" or "MediaPipe"
    val sizeMb:      Float,
    val contextLen:  Int,
    val tokensPerSec: Float = 0f,  // populated after first inference
)

// ── Backend interface ─────────────────────────────────────────────────────────

/**
 * Common contract every on-device (or LAN) LLM backend must implement.
 *
 * Lifecycle:
 *   [load] → [generate] (many times) → [unload]
 *
 * All implementations must:
 *   - Be safe to call from any coroutine context (use Dispatchers.IO internally)
 *   - Emit tokens as they are produced (streaming, not buffered)
 *   - Complete the returned [Flow] when generation ends or is stopped
 *   - Clean up all native resources in [unload]
 */
interface LocalLlmBackend {

    /** Stable identifier — used by IntelliRouter and the model registry. */
    val backendId: String

    /** True between a successful [load] and [unload]. */
    val isLoaded: Boolean

    /**
     * Load the model and prepare for inference.
     * May block for several seconds on a cold start.
     * Calling [load] on an already-loaded backend is a no-op.
     *
     * @throws IllegalStateException if the model file is not found
     * @throws RuntimeException on native backend failure
     */
    suspend fun load(config: LlmLoadConfig)

    /**
     * Run inference on [prompt], emitting string pieces as they are generated.
     *
     * The prompt must already be formatted for the model (system + user turns).
     * Use [PromptFormatter] to build the correct template.
     *
     * The flow completes when:
     *   - EOS/EOT token is sampled
     *   - [maxNewTokens] is reached
     *   - The caller cancels the flow's coroutine
     *
     * @throws IllegalStateException if [isLoaded] is false
     */
    fun generate(prompt: String, config: GenerationConfig = GenerationConfig()): Flow<String>

    /**
     * Stop any in-progress generation immediately (token-boundary accurate).
     * Non-blocking. Safe to call even when no generation is running.
     */
    fun stop()

    /**
     * Free all resources (native context, GPU memory, etc.).
     * After this call [isLoaded] is false.
     */
    suspend fun unload()

    /**
     * Metadata snapshot. Only valid after [load]; returns a default struct otherwise.
     */
    fun info(): LlmInfo
}
