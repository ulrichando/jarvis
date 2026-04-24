package com.jarvis.android.domain.model

import kotlinx.serialization.Serializable

/**
 * Per-model inference configuration — the knobs a user tunes from the
 * "Configurations" bottom sheet (mirrors Google AI Edge Gallery's per-model
 * config dialog).
 *
 * Defaults match Gallery's `model_allowlists/1_0_12.json`:
 *   - Gemma 4 / Gemma 3n / Gemma 3 1B: topK=64, topP=0.95, temperature=1.0
 *   - Qwen / DeepSeek: same defaults inherited from Gallery
 *
 * Stored JSON-encoded in [com.jarvis.android.data.repository.ApiKeyProviderImpl]
 * under the key `model_config:<modelId>` so each model remembers its own
 * tuning across launches.
 */
@Serializable
data class ModelConfig(
    /** GPU (Adreno / Mali OpenCL) or CPU. Falls back to CPU if GPU init fails. */
    val accelerator: Accelerator = Accelerator.GPU,

    /** Maximum tokens the engine will keep in the context window. */
    val maxTokens: Int = 4000,

    /** Sampler top-k — number of highest-probability tokens to consider. */
    val topK: Int = 64,

    /** Sampler top-p — nucleus sampling probability cutoff. */
    val topP: Float = 0.95f,

    /** Sampler temperature — higher = more creative, lower = more deterministic. */
    val temperature: Float = 1.0f,

    /**
     * If true, keep Gemma 4 / DeepSeek R1 thinking-channel traces in the
     * displayed output so the user sees the model's reasoning. Off by default
     * because the traces can be verbose and clutter short answers.
     */
    val enableThinking: Boolean = false,
) {
    enum class Accelerator { GPU, CPU }
}
