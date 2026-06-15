package com.jarvis.android.domain.model

/**
 * A single entry in the JARVIS model catalog.
 *
 * Instances are either:
 *   - Pre-populated from [ModelRegistry] (the built-in catalog)
 *   - Created by the user via the custom URL import flow
 *
 * ── Lifecycle ────────────────────────────────────────────────────────────────
 *
 *  Catalog entry
 *       │
 *       ▼  [DownloadState.NotDownloaded]
 *  User taps Download
 *       │
 *       ▼  [DownloadState.Downloading(progress)]
 *  ModelDownloader completes + SHA-256 verified
 *       │
 *       ▼  [DownloadState.Downloaded(localPath)]
 *  User taps Load
 *       │
 *       ▼  [DownloadState.Loaded]   ← model is in GPU/RAM, ready to infer
 */
data class ModelEntry(

    // ── Identity ─────────────────────────────────────────────────────────────

    /** Stable unique identifier (slug). e.g. "gemma4-4b-q4km", "llama32-1b-q8". */
    val id: String,

    /** Human-readable display name. e.g. "Gemma 4 4B Q4_K_M". */
    val name: String,

    /** Model family slug. e.g. "gemma4", "llama3", "phi4", "qwen25". */
    val family: String,

    // ── Size / hardware ───────────────────────────────────────────────────────

    /** Parameter count as a display string. e.g. "1B", "4B", "7B". */
    val paramCount: String,

    /** Quantisation format. e.g. "Q4_K_M", "Q8_0", "F16", "MediaPipe". */
    val quantization: String,

    /** Compressed file size in bytes (what will be downloaded). */
    val sizeBytes: Long,

    /** Minimum RAM required for inference (MB). Used for device compatibility badge. */
    val ramRequiredMb: Int,

    // ── Routing ───────────────────────────────────────────────────────────────

    /** Which inference backend runs this model. */
    val backend: ModelBackend,

    // ── Download ──────────────────────────────────────────────────────────────

    /** Primary download URL (HuggingFace GGUF / Google AI Hub .task). */
    val downloadUrl: String,

    /** SHA-256 hex digest of the downloaded file. Empty string = skip verification. */
    val sha256: String = "",

    /** Alternate mirror URLs tried in order if [downloadUrl] fails. */
    val mirrorUrls: List<String> = emptyList(),

    // ── Capabilities ─────────────────────────────────────────────────────────

    val capabilities: Set<ModelCapability> = setOf(ModelCapability.CHAT),

    /** Maximum context window supported by this model (tokens). */
    val contextLength: Int = 2048,

    // ── Metadata ──────────────────────────────────────────────────────────────

    /** License identifier. e.g. "gemma", "llama3", "apache-2.0", "mit". */
    val license: String = "",

    /** Optional short description shown in the model card. */
    val description: String = "",

    // ── Runtime state (mutable, not persisted in catalog) ────────────────────

    /** Current download / load state. Updated by ModelDownloader / ModelLoader. */
    val downloadState: DownloadState = DownloadState.NotDownloaded,

    /** True if this entry was added by the user (custom URL import). */
    val isCustom: Boolean = false,

    /** Absolute path to the local file once downloaded. Null if not yet on device. */
    val localPath: String? = null,
)

// ── Capability enum ───────────────────────────────────────────────────────────

enum class ModelCapability(val label: String) {
    CHAT("Chat"),
    CODE("Code"),
    REASONING("Reasoning"),
    VISION("Vision"),
    EMBEDDING("Embedding"),
    FUNCTION_CALLING("Tools"),
}

// ── Download / load state machine ─────────────────────────────────────────────

sealed interface DownloadState {

    /** File not present on device. */
    data object NotDownloaded : DownloadState

    /**
     * WorkManager job is active.
     * @param progress 0.0 – 1.0
     * @param downloadedBytes Bytes received so far.
     * @param totalBytes Total file size (-1 if unknown).
     */
    data class Downloading(
        val progress:       Float = 0f,
        val downloadedBytes: Long = 0L,
        val totalBytes:     Long = -1L,
        val workerId:       String = "",
    ) : DownloadState

    /** Download complete, SHA-256 verified. */
    data class Downloaded(val localPath: String) : DownloadState

    /** Model is in GPU/RAM and accepting inference requests. */
    data object Loaded : DownloadState

    /** Download or verification failed. */
    data class Failed(val reason: String) : DownloadState
}

// ── Routing mode ─────────────────────────────────────────────────────────────

/**
 * Controls which AI backend [IntelliRouter] sends queries to.
 * Persisted in SharedPreferences; surfaced as a user toggle in the chat input bar.
 */
enum class RoutingMode(val label: String, val description: String) {
    AUTO(
        label       = "Auto",
        description = "JARVIS picks the best backend per query",
    ),
    LOCAL(
        label       = "Local",
        description = "Always use the on-device model (offline / private)",
    ),
    CLOUD(
        label       = "Cloud",
        description = "Always use the Anthropic API (best quality)",
    ),
    HYBRID(
        label       = "Hybrid",
        description = "Local draft → cloud refinement",
    ),
}

// ── Benchmark result ──────────────────────────────────────────────────────────

/**
 * Results of a single benchmark run on a model.
 * Produced by [BenchmarkModelUseCase] and stored for comparison in the UI.
 */
data class BenchmarkResult(
    val modelId:          String,
    val modelName:        String,
    /** Time from sending the first token to receiving the first output token (ms). */
    val ttftMs:           Long,
    /** Average tokens generated per second over the full run. */
    val tokensPerSec:     Float,
    /** Peak RAM consumed during inference (MB). */
    val peakRamMb:        Int,
    /** Peak CPU utilisation percentage (0–100). */
    val peakCpuPct:       Int,
    /** GPU layers actually used (0 if CPU-only). */
    val gpuLayers:        Int,
    /** Number of tokens generated in the benchmark run. */
    val totalTokens:      Int,
    /** Unix millis when the benchmark was run. */
    val timestampMs:      Long = System.currentTimeMillis(),
)

// ── Display helpers ───────────────────────────────────────────────────────────

/** Formatted file size string: "2.4 GB", "800 MB". */
val ModelEntry.sizeFormatted: String
    get() = when {
        sizeBytes >= 1_073_741_824L -> "%.1f GB".format(sizeBytes / 1_073_741_824.0)
        sizeBytes >= 1_048_576L     -> "%.0f MB".format(sizeBytes / 1_048_576.0)
        sizeBytes > 0L              -> "%.0f KB".format(sizeBytes / 1_024.0)
        else                        -> "Unknown"
    }

/** True if the model file is present and ready to load. */
val ModelEntry.isOnDevice: Boolean
    get() = downloadState is DownloadState.Downloaded || downloadState is DownloadState.Loaded

/** Estimated RAM footprint label for the device compatibility badge. */
val ModelEntry.ramLabel: String
    get() = when {
        ramRequiredMb >= 8_000 -> "${ramRequiredMb / 1_024} GB"
        else                   -> "$ramRequiredMb MB"
    }
