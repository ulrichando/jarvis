package com.jarvis.android.system.llm

import com.jarvis.android.data.repository.ModelRegistrySource
import com.jarvis.android.domain.model.DownloadState
import com.jarvis.android.domain.model.ModelBackend
import com.jarvis.android.domain.model.ModelCapability
import com.jarvis.android.domain.model.ModelEntry
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Built-in model catalog for JARVIS on-device inference.
 *
 * The catalog mirrors Google's own AI Edge Gallery model allowlist
 * (`model_allowlists/1_0_12.json`) — LiteRT-LM `.litertlm` bundles from the
 * `litert-community` and `google` organisations on HuggingFace. These are the
 * same models and quantisations that ship in Google's reference on-device
 * inference app and are the ones LiteRT-LM is actually tested against.
 *
 * Previous catalog (GGUF + Ollama + MediaPipe entries) has been removed — the
 * user-visible download list is now LiteRT-LM only. LlamaJNI / OllamaBridge
 * remain in the backend graph for user-imported custom models but are not
 * exposed in the catalog UI.
 *
 * ## Download URL shape
 *
 * HuggingFace LFS canonical resolve path:
 *   https://huggingface.co/<modelId>/resolve/<commitHash>/<modelFile>
 *
 * Pinning to the exact commit hash (not "main") means the app downloads the
 * same weights Google tested with, even if upstream re-uploads a newer build.
 */
@Singleton
class ModelRegistry @Inject constructor() : ModelRegistrySource {

    override fun getAll(): List<ModelEntry> = CATALOG

    companion object {

        /**
         * The built-in catalog.
         *
         * Ordered small → large within each family. Gemma 4 and Gemma 3n are
         * multimodal (text + image + audio). Gemma 3 1B, Qwen, and DeepSeek
         * are text-only. Tiny Garden and Mobile Actions are specialist 270M
         * fine-tunes (function-calling / agentic tasks) that fit on any phone.
         */
        val CATALOG: List<ModelEntry> = listOf(

            // ── Gemma 4 — newest multimodal, requires 8 GB+ RAM ──────────────

            liteRtLmEntry(
                id              = "gemma-4-e2b-it",
                name            = "Gemma 4 E2B IT",
                family          = "gemma4",
                paramCount      = "2B",
                hfModelId       = "litert-community/gemma-4-E2B-it-litert-lm",
                modelFile       = "gemma-4-E2B-it.litertlm",
                commitHash      = "7fa1d78473894f7e736a21d920c3aa80f950c0db",
                sizeBytes       = 2_583_085_056L,
                ramRequiredMb   = 8_192,
                contextLength   = 32_000,
                capabilities    = setOf(
                    ModelCapability.CHAT, ModelCapability.REASONING, ModelCapability.VISION,
                ),
                description     = "Gemma 4 E2B Instruct — multimodal (text, image, audio), 32K context. Best all-round on-device model for 8 GB+ devices.",
            ),

            liteRtLmEntry(
                id              = "gemma-4-e4b-it",
                name            = "Gemma 4 E4B IT",
                family          = "gemma4",
                paramCount      = "4B",
                hfModelId       = "litert-community/gemma-4-E4B-it-litert-lm",
                modelFile       = "gemma-4-E4B-it.litertlm",
                commitHash      = "9695417f248178c63a9f318c6e0c56cb917cb837",
                sizeBytes       = 3_654_467_584L,
                ramRequiredMb   = 12_288,
                contextLength   = 32_000,
                capabilities    = setOf(
                    ModelCapability.CHAT, ModelCapability.REASONING, ModelCapability.VISION,
                ),
                description     = "Gemma 4 E4B Instruct — flagship multimodal (text, image, audio), 32K context. Requires 12 GB RAM.",
            ),

            // ── Gemma 3n — multimodal with audio ─────────────────────────────

            liteRtLmEntry(
                id              = "gemma-3n-e2b-it",
                name            = "Gemma 3n E2B IT",
                family          = "gemma3n",
                paramCount      = "2B",
                hfModelId       = "google/gemma-3n-E2B-it-litert-lm",
                modelFile       = "gemma-3n-E2B-it-int4.litertlm",
                commitHash      = "ba9ca88da013b537b6ed38108be609b8db1c3a16",
                sizeBytes       = 3_655_827_456L,
                ramRequiredMb   = 8_192,
                contextLength   = 4_096,
                capabilities    = setOf(
                    ModelCapability.CHAT, ModelCapability.VISION,
                ),
                description     = "Gemma 3n E2B — text + vision + audio input. 4K context. Best on-device audio model.",
            ),

            liteRtLmEntry(
                id              = "gemma-3n-e4b-it",
                name            = "Gemma 3n E4B IT",
                family          = "gemma3n",
                paramCount      = "4B",
                hfModelId       = "google/gemma-3n-E4B-it-litert-lm",
                modelFile       = "gemma-3n-E4B-it-int4.litertlm",
                commitHash      = "297ed75955702dec3503e00c2c2ecbbf475300bc",
                sizeBytes       = 4_919_541_760L,
                ramRequiredMb   = 12_288,
                contextLength   = 4_096,
                capabilities    = setOf(
                    ModelCapability.CHAT, ModelCapability.VISION,
                ),
                description     = "Gemma 3n E4B — flagship multimodal (text, image, audio), 4K context. Requires 12 GB RAM.",
            ),

            // ── Gemma 3 1B — ultra-light, runs on any recent phone ──────────

            liteRtLmEntry(
                id              = "gemma3-1b-it",
                name            = "Gemma 3 1B IT",
                family          = "gemma3",
                paramCount      = "1B",
                hfModelId       = "litert-community/Gemma3-1B-IT",
                modelFile       = "gemma3-1b-it-int4.litertlm",
                commitHash      = "42d538a932e8d5b12e6b3b455f5572560bd60b2c",
                sizeBytes       = 584_417_280L,
                ramRequiredMb   = 6_144,
                contextLength   = 4_096,
                capabilities    = setOf(ModelCapability.CHAT),
                description     = "Gemma 3 1B Instruct — Google's smallest LLM, 4-bit quantised. Under 600 MB, runs anywhere.",
            ),

            // ── Qwen 2.5 1.5B — balanced general-purpose ─────────────────────

            liteRtLmEntry(
                id              = "qwen25-15b-instruct",
                name            = "Qwen 2.5 1.5B Instruct",
                family          = "qwen25",
                paramCount      = "1.5B",
                hfModelId       = "litert-community/Qwen2.5-1.5B-Instruct",
                modelFile       = "Qwen2.5-1.5B-Instruct_multi-prefill-seq_q8_ekv4096.litertlm",
                commitHash      = "19edb84c69a0212f29a6ef17ba0d6f278b6a1614",
                sizeBytes       = 1_597_931_520L,
                ramRequiredMb   = 6_144,
                contextLength   = 4_096,
                capabilities    = setOf(ModelCapability.CHAT, ModelCapability.CODE),
                description     = "Qwen 2.5 1.5B Instruct — Alibaba's general-purpose model, strong at code and multilingual chat.",
            ),

            // ── DeepSeek R1 Distill — reasoning ──────────────────────────────

            liteRtLmEntry(
                id              = "deepseek-r1-distill-qwen-15b",
                name            = "DeepSeek R1 Distill Qwen 1.5B",
                family          = "deepseek",
                paramCount      = "1.5B",
                hfModelId       = "litert-community/DeepSeek-R1-Distill-Qwen-1.5B",
                modelFile       = "DeepSeek-R1-Distill-Qwen-1.5B_multi-prefill-seq_q8_ekv4096.litertlm",
                commitHash      = "e34bb88632342d1f9640bad579a45134eb1cf988",
                sizeBytes       = 1_833_451_520L,
                ramRequiredMb   = 6_144,
                contextLength   = 4_096,
                capabilities    = setOf(ModelCapability.CHAT, ModelCapability.REASONING),
                description     = "DeepSeek R1 distilled into Qwen 1.5B — strong step-by-step reasoning (<thinking> traces).",
            ),

            // ── Tiny specialist models — 270M, fits anywhere ─────────────────

            liteRtLmEntry(
                id              = "tinygarden-270m",
                name            = "Tiny Garden 270M",
                family          = "functiongemma",
                paramCount      = "270M",
                hfModelId       = "litert-community/functiongemma-270m-ft-tiny-garden",
                modelFile       = "tiny_garden_q8_ekv1024.litertlm",
                commitHash      = "c205853ff82da86141a1105faa2344a8b176dfe7",
                sizeBytes       = 288_964_608L,
                ramRequiredMb   = 4_096,
                contextLength   = 1_024,
                capabilities    = setOf(ModelCapability.FUNCTION_CALLING),
                description     = "Function-tuned Gemma 270M for the Tiny Garden agent task. Specialist model — sub-300 MB.",
            ),

            liteRtLmEntry(
                id              = "mobileactions-270m",
                name            = "Mobile Actions 270M",
                family          = "functiongemma",
                paramCount      = "270M",
                hfModelId       = "litert-community/functiongemma-270m-ft-mobile-actions",
                modelFile       = "mobile_actions_q8_ekv1024.litertlm",
                commitHash      = "38942192c9b723af836d489074823ff33d4a3e7a",
                sizeBytes       = 288_964_608L,
                ramRequiredMb   = 4_096,
                contextLength   = 1_024,
                capabilities    = setOf(ModelCapability.FUNCTION_CALLING),
                description     = "Function-tuned Gemma 270M for Android mobile actions (agentic tool use). Under 300 MB.",
            ),
        )
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Build a canonical HuggingFace LFS URL pinned to a specific commit. */
private fun hfUrl(modelId: String, commitHash: String, modelFile: String): String =
    "https://huggingface.co/$modelId/resolve/$commitHash/$modelFile"

/** Concise factory so each catalog entry reads as data, not plumbing. */
private fun liteRtLmEntry(
    id:             String,
    name:           String,
    family:         String,
    paramCount:     String,
    hfModelId:      String,
    modelFile:      String,
    commitHash:     String,
    sizeBytes:      Long,
    ramRequiredMb:  Int,
    contextLength:  Int,
    capabilities:   Set<ModelCapability>,
    description:    String,
): ModelEntry = ModelEntry(
    id            = id,
    name          = name,
    family        = family,
    paramCount    = paramCount,
    quantization  = "LiteRT-LM",
    sizeBytes     = sizeBytes,
    ramRequiredMb = ramRequiredMb,
    backend       = ModelBackend.LITERTLM,
    downloadUrl   = hfUrl(hfModelId, commitHash, modelFile),
    sha256        = "",   // HF pins by commitHash; file integrity is inherent.
    capabilities  = capabilities,
    contextLength = contextLength,
    license       = "gemma",
    description   = description,
    downloadState = DownloadState.NotDownloaded,
)
