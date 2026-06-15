package com.jarvis.android.data.local.entity

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey
import com.jarvis.android.domain.model.DownloadState
import com.jarvis.android.domain.model.ModelBackend
import com.jarvis.android.domain.model.ModelCapability
import com.jarvis.android.domain.model.ModelEntry

/**
 * Persisted representation of a [ModelEntry] in the `local_models` Room table.
 *
 * Two categories of rows live here:
 *   1. **Catalog entries** — seeded from [ModelRegistry] on first launch,
 *      updated when the registry version bumps (upsert on conflict).
 *   2. **Custom entries** — added by the user via the "Import URL" flow.
 *      These have [isCustom] = true and are never overwritten by catalog refreshes.
 *
 * Schema decisions:
 *   - [downloadState] is stored as a plain string enum name; the more complex
 *     [DownloadState.Downloading] sub-state is ephemeral and never persisted
 *     (it lives only in the ViewModel's StateFlow while a job is active).
 *   - [capabilities] is a comma-separated enum name string (e.g. "CHAT,CODE").
 *     Kept simple — no need for a join table for a small fixed set.
 *   - [sha256] may be empty for user-imported custom models.
 *   - [localPath] is null until the download completes successfully.
 *   - [mirrorUrls] is a newline-separated list of fallback URLs.
 */
@Entity(
    tableName = "local_models",
    indices = [
        Index("backend"),
        Index("family"),
        Index("download_state"),
        Index("is_custom"),
    ],
)
data class ModelEntity(

    /** Stable catalog slug — e.g. "gemma4-4b-q4km". Primary key. */
    @PrimaryKey
    val id: String,

    val name: String,

    /** Model family: "gemma4", "llama3", "phi4", "qwen25", etc. */
    val family: String,

    /** Parameter count display string: "1B", "4B", "7B". */
    @ColumnInfo(name = "param_count")
    val paramCount: String,

    /** Quantisation: "Q4_K_M", "Q8_0", "F16", "MediaPipe". */
    val quantization: String,

    /** Compressed download size in bytes. */
    @ColumnInfo(name = "size_bytes")
    val sizeBytes: Long,

    /** Minimum device RAM required for inference (MB). */
    @ColumnInfo(name = "ram_required_mb")
    val ramRequiredMb: Int,

    /** Inference backend enum name (e.g. "LLAMACPP"). */
    val backend: String,

    /** Primary download URL. */
    @ColumnInfo(name = "download_url")
    val downloadUrl: String,

    /** SHA-256 hex digest for integrity verification. Empty = skip. */
    val sha256: String = "",

    /** Newline-separated mirror URLs, tried in order on primary failure. */
    @ColumnInfo(name = "mirror_urls")
    val mirrorUrls: String = "",

    /** Comma-separated [ModelCapability] names: "CHAT,CODE,REASONING". */
    val capabilities: String = "CHAT",

    /** Maximum context window (tokens). */
    @ColumnInfo(name = "context_length")
    val contextLength: Int = 2048,

    val license: String = "",

    val description: String = "",

    /**
     * Persisted download state.
     * Only [DownloadState.NotDownloaded], [DownloadState.Downloaded],
     * and [DownloadState.Failed] are stored. [DownloadState.Downloading]
     * and [DownloadState.Loaded] are runtime-only.
     *
     * Values: "NOT_DOWNLOADED" | "DOWNLOADED" | "FAILED:<reason>"
     */
    @ColumnInfo(name = "download_state")
    val downloadState: String = "NOT_DOWNLOADED",

    /** Absolute path to the local .gguf / .task file. Null until downloaded. */
    @ColumnInfo(name = "local_path")
    val localPath: String? = null,

    /** True for user-imported entries not in the built-in catalog. */
    @ColumnInfo(name = "is_custom")
    val isCustom: Boolean = false,

    /** Unix millis when this entry was last updated in the DB. */
    @ColumnInfo(name = "updated_at")
    val updatedAt: Long = System.currentTimeMillis(),
)

// ── Mapping: Entity → Domain ──────────────────────────────────────────────────

fun ModelEntity.toDomain(): ModelEntry = ModelEntry(
    id            = id,
    name          = name,
    family        = family,
    paramCount    = paramCount,
    quantization  = quantization,
    sizeBytes     = sizeBytes,
    ramRequiredMb = ramRequiredMb,
    // Decode defensively: a row persisted by an older build may carry a backend
    // string that no longer exists in the enum (e.g. the removed OLLAMA /
    // OPENAI_COMPAT local-server backends). Map any unknown value to the
    // deprecated MEDIAPIPE sentinel, which backendFor() refuses to dispatch and
    // purgeLegacyMediaPipeRows() cleans up — never crash on an old row.
    backend       = ModelBackend.entries.firstOrNull { it.name == backend } ?: ModelBackend.MEDIAPIPE,
    downloadUrl   = downloadUrl,
    sha256        = sha256,
    mirrorUrls    = mirrorUrls.lines().filter { it.isNotBlank() },
    capabilities  = capabilities.split(',')
        .mapNotNull { name -> ModelCapability.entries.find { it.name == name.trim() } }
        .toSet()
        .ifEmpty { setOf(ModelCapability.CHAT) },
    contextLength = contextLength,
    license       = license,
    description   = description,
    downloadState = parseDownloadState(downloadState, localPath),
    isCustom      = isCustom,
    localPath     = localPath,
)

// ── Mapping: Domain → Entity ──────────────────────────────────────────────────

fun ModelEntry.toEntity(): ModelEntity = ModelEntity(
    id            = id,
    name          = name,
    family        = family,
    paramCount    = paramCount,
    quantization  = quantization,
    sizeBytes     = sizeBytes,
    ramRequiredMb = ramRequiredMb,
    backend       = backend.name,
    downloadUrl   = downloadUrl,
    sha256        = sha256,
    mirrorUrls    = mirrorUrls.joinToString("\n"),
    capabilities  = capabilities.joinToString(",") { it.name },
    contextLength = contextLength,
    license       = license,
    description   = description,
    downloadState = serializeDownloadState(downloadState),
    isCustom      = isCustom,
    localPath     = localPath,
    updatedAt     = System.currentTimeMillis(),
)

// ── State serialisation helpers ───────────────────────────────────────────────

private fun serializeDownloadState(state: DownloadState): String = when (state) {
    is DownloadState.NotDownloaded -> "NOT_DOWNLOADED"
    is DownloadState.Downloaded    -> "DOWNLOADED"
    is DownloadState.Loaded        -> "DOWNLOADED"          // Loaded is runtime-only; persist as Downloaded
    is DownloadState.Downloading   -> "DOWNLOADING:${state.progress}"
    is DownloadState.Failed        -> "FAILED:${state.reason.take(200)}"
}

private fun parseDownloadState(raw: String, localPath: String?): DownloadState = when {
    raw == "NOT_DOWNLOADED"         -> DownloadState.NotDownloaded
    raw == "DOWNLOADED"             -> if (localPath != null) DownloadState.Downloaded(localPath)
                                       else DownloadState.NotDownloaded
    raw.startsWith("DOWNLOADING:") -> DownloadState.Downloading(
                                           raw.removePrefix("DOWNLOADING:").toFloatOrNull() ?: 0f
                                       )
    raw.startsWith("FAILED:")       -> DownloadState.Failed(raw.removePrefix("FAILED:"))
    else                            -> DownloadState.NotDownloaded
}
