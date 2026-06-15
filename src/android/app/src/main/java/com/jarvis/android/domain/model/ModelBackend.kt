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
     * Google MediaPipe AI Edge Tasks GenAI — legacy.
     * DEPRECATED: segfaults inside `drishti` thread on Samsung. Replaced by
     * [LITERTLM]. Kept in the enum so older DB rows can still be decoded
     * (and purged) on app upgrade, never dispatched.
     */
    MEDIAPIPE(
        label      = "MediaPipe",
        extensions = listOf(".task"),
    ),

    /**
     * Google AI Edge LiteRT-LM — current on-device inference runtime.
     * Models are `.litertlm` bundles distributed via HuggingFace
     * (litert-community and google orgs). Supports CPU, GPU (OpenCL),
     * and NPU (Hexagon on Snapdragon, Neural Engine on Exynos) backends.
     * Powers every catalog entry that came from Google's AI Edge Gallery.
     */
    LITERTLM(
        label      = "LiteRT-LM",
        extensions = listOf(".litertlm", ".task"),
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
}
