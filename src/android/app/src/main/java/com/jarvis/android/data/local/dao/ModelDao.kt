package com.jarvis.android.data.local.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Update
import com.jarvis.android.data.local.entity.ModelEntity
import kotlinx.coroutines.flow.Flow

/**
 * Room DAO for the `local_models` table.
 *
 * Read patterns:
 *   - [observeAll] / [observeByBackend] — Flow for reactive UI (model library screen)
 *   - [getById] / [getDownloaded] — one-shot for repository operations
 *
 * Write patterns:
 *   - [upsertCatalog] — called by ModelRegistry to seed / refresh built-in entries
 *     without overwriting user-set [localPath] or [downloadState] on existing rows
 *   - [insert] — used for custom user-imported entries
 *   - Targeted [update*] helpers — only write the columns that changed, preserving
 *     the rest to minimise WAL churn
 */
@Dao
interface ModelDao {

    // ── Observe (Flow — reactive) ─────────────────────────────────────────────

    /** All models, custom entries first, then ordered by family + param count. */
    @Query("""
        SELECT * FROM local_models
        ORDER BY is_custom DESC, family ASC, size_bytes ASC
    """)
    fun observeAll(): Flow<List<ModelEntity>>

    /** Models filtered to a specific backend enum name. */
    @Query("""
        SELECT * FROM local_models
        WHERE backend = :backendName
        ORDER BY size_bytes ASC
    """)
    fun observeByBackend(backendName: String): Flow<List<ModelEntity>>

    /** Models that are on-device (downloaded or loaded). */
    @Query("""
        SELECT * FROM local_models
        WHERE download_state = 'DOWNLOADED'
        ORDER BY updated_at DESC
    """)
    fun observeDownloaded(): Flow<List<ModelEntity>>

    // ── One-shot reads ────────────────────────────────────────────────────────

    @Query("SELECT * FROM local_models WHERE id = :id")
    suspend fun getById(id: String): ModelEntity?

    /** All models with a local file present (used by IntelliRouter at startup). */
    @Query("SELECT * FROM local_models WHERE download_state = 'DOWNLOADED'")
    suspend fun getDownloaded(): List<ModelEntity>

    /** All custom user-imported entries. */
    @Query("SELECT * FROM local_models WHERE is_custom = 1")
    suspend fun getCustom(): List<ModelEntity>

    /** Total bytes used by all downloaded models on device. */
    @Query("""
        SELECT COALESCE(SUM(size_bytes), 0)
        FROM local_models
        WHERE download_state = 'DOWNLOADED'
    """)
    suspend fun getTotalDownloadedBytes(): Long

    // ── Write ─────────────────────────────────────────────────────────────────

    /**
     * Insert a new custom entry.
     * Uses ABORT on conflict so duplicate IDs surface as an exception.
     */
    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insert(model: ModelEntity)

    /**
     * Upsert a batch of catalog entries from [ModelRegistry].
     *
     * Uses IGNORE on conflict: if a row with the same [ModelEntity.id] already
     * exists (e.g. user has already downloaded the model) the existing row is
     * preserved untouched, so [downloadState] / [localPath] are never wiped.
     *
     * If the catalog wants to push a metadata update (name, description, URL
     * change) to an existing row, call [updateCatalogMetadata] instead.
     */
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun upsertCatalog(models: List<ModelEntity>)

    @Update
    suspend fun update(model: ModelEntity)

    // ── Targeted column updates (avoid full-row rewrites) ─────────────────────

    /**
     * Record that a download is in progress with [progress] in [0.0, 1.0].
     * Called periodically by [DownloadWorker] so the UI shows a live progress bar.
     */
    @Query("""
        UPDATE local_models
        SET download_state = 'DOWNLOADING:' || :progress,
            updated_at     = :now
        WHERE id = :id
    """)
    suspend fun markDownloading(
        id:       String,
        progress: Float,
        now:      Long = System.currentTimeMillis(),
    )

    /**
     * Record a completed download.
     * Sets state to "DOWNLOADED" and writes the local file path.
     */
    @Query("""
        UPDATE local_models
        SET download_state = 'DOWNLOADED',
            local_path     = :localPath,
            updated_at     = :now
        WHERE id = :id
    """)
    suspend fun markDownloaded(
        id:        String,
        localPath: String,
        now:       Long = System.currentTimeMillis(),
    )

    /**
     * Record a failed download with a human-readable reason.
     * Keeps [localPath] unchanged so a partial file isn't orphaned.
     */
    @Query("""
        UPDATE local_models
        SET download_state = 'FAILED:' || :reason,
            updated_at     = :now
        WHERE id = :id
    """)
    suspend fun markFailed(
        id:     String,
        reason: String,
        now:    Long = System.currentTimeMillis(),
    )

    /**
     * Reset to NOT_DOWNLOADED (e.g. after user deletes the local file).
     */
    @Query("""
        UPDATE local_models
        SET download_state = 'NOT_DOWNLOADED',
            local_path     = NULL,
            updated_at     = :now
        WHERE id = :id
    """)
    suspend fun markDeleted(
        id:  String,
        now: Long = System.currentTimeMillis(),
    )

    /**
     * Update catalog-provided metadata columns without touching download state.
     * Called when ModelRegistry ships a newer version of the catalog.
     */
    @Query("""
        UPDATE local_models
        SET name          = :name,
            description   = :description,
            download_url  = :downloadUrl,
            sha256        = :sha256,
            size_bytes    = :sizeBytes,
            ram_required_mb = :ramRequiredMb,
            updated_at    = :now
        WHERE id = :id AND is_custom = 0
    """)
    suspend fun updateCatalogMetadata(
        id:           String,
        name:         String,
        description:  String,
        downloadUrl:  String,
        sha256:       String,
        sizeBytes:    Long,
        ramRequiredMb: Int,
        now:          Long = System.currentTimeMillis(),
    )

    // ── Delete ────────────────────────────────────────────────────────────────

    /** Remove a custom entry entirely. Catalog entries should use [markDeleted]. */
    @Query("DELETE FROM local_models WHERE id = :id AND is_custom = 1")
    suspend fun deleteCustom(id: String)

    /**
     * Remove all non-custom catalog entries that are NOT in [keepIds] and have
     * never been downloaded (state = 'NOT_DOWNLOADED').
     *
     * Called during [refreshCatalog] to purge stale or gated entries from
     * previous app versions without touching anything the user has downloaded.
     *
     * Note: Room's @Query does not support IN with a variable-length list directly;
     * we use a raw delete approach via [deleteStaleByIdList].
     */
    @Query("""
        DELETE FROM local_models
        WHERE is_custom = 0
          AND download_state = 'NOT_DOWNLOADED'
          AND id NOT IN (:keepIds)
    """)
    suspend fun deleteStaleNotDownloaded(keepIds: List<String>)

    /** Wipe the entire table — used for full reset in Settings. */
    @Query("DELETE FROM local_models")
    suspend fun deleteAll()
}
