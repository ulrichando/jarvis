package com.jarvis.android.domain.model

/**
 * Which inference engine a model runs on.
 *
 * Used in [ModelEntry] to determine:
 *   - which [LocalLlmBackend] implementation to instantiate
 *   - which file format to download / verify
 *   - which UI labels and badges to show
 */
enum class ModelBackend(
    /** Short label shown in UI chips. */
    val label: String,
    /** File extension(s) expected for this backend. */
    val extensions: List<String>,
) {
    /**
     * Google MediaPipe AI Edge — official Gemma 4 support.
     * Models are `.task` bundles (TensorFlow Lite + metadata).
     * Accelerated via GPU delegate (OpenCL / OpenGL).
     */
    MEDIAPIPE(
        label      = "MediaPipe",
        extensions = listOf(".task"),
    ),

    /**
     * llama.cpp via JNI — universal GGUF loader.
     * Supports any quantised model (Q4_K_M, Q8_0, F16 …).
     * Accelerated via ggml-vulkan on capable devices.
     */
    LLAMACPP(
        label      = "llama.cpp",
        extensions = listOf(".gguf"),
    ),

    /**
     * Ollama REST bridge — model runs on a LAN server.
     * No local file; model name used as server-side identifier.
     * Fallback to on-device if server is unreachable.
     */
    OLLAMA(
        label      = "Ollama",
        extensions = emptyList(),   // server-managed
    ),

    /**
     * OpenAI-compatible endpoint (LM Studio, vLLM, llama-server).
     * Same protocol as Ollama but with /v1/chat/completions.
     */
    OPENAI_COMPAT(
        label      = "OpenAI API",
        extensions = emptyList(),
    ),
}
