package com.jarvis.android.domain.usecase.llm

import com.jarvis.android.domain.model.BenchmarkResult
import com.jarvis.android.domain.model.ModelBackend
import com.jarvis.android.domain.model.ModelEntry
import com.jarvis.android.domain.model.RoutingMode
import com.jarvis.android.domain.repository.ModelRepository
import com.jarvis.android.system.llm.GenerationConfig
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.StateFlow
import javax.inject.Inject

// ── Catalog ───────────────────────────────────────────────────────────────────

/**
 * Observe the full model catalog (built-in + custom) as a reactive stream.
 * Emits a new list whenever download state, custom entries, or catalog data change.
 */
class ObserveModelsUseCase @Inject constructor(
    private val repo: ModelRepository,
) {
    operator fun invoke(): Flow<List<ModelEntry>> = repo.observeModels()
}

/**
 * Observe only models that have a local file present and are ready to load.
 */
class ObserveDownloadedModelsUseCase @Inject constructor(
    private val repo: ModelRepository,
) {
    operator fun invoke(): Flow<List<ModelEntry>> = repo.observeDownloaded()
}

/**
 * Seed the built-in catalog from [ModelRegistry] without touching existing
 * download state. Safe to call on every app launch — rows already present
 * are ignored (UPSERT with IGNORE conflict strategy).
 */
class RefreshModelCatalogUseCase @Inject constructor(
    private val repo: ModelRepository,
) {
    suspend operator fun invoke() = repo.refreshCatalog()
}

// ── Download ──────────────────────────────────────────────────────────────────

/**
 * Enqueue a resumable background download for [modelId].
 * Progress flows back through [ObserveModelsUseCase] as [DownloadState.Downloading].
 */
class DownloadModelUseCase @Inject constructor(
    private val repo: ModelRepository,
) {
    suspend operator fun invoke(modelId: String) = repo.startDownload(modelId)
}

/**
 * Cancel a download that is currently in progress.
 * Any partially downloaded bytes are removed.
 */
class CancelDownloadUseCase @Inject constructor(
    private val repo: ModelRepository,
) {
    suspend operator fun invoke(modelId: String) = repo.cancelDownload(modelId)
}

/**
 * Delete the local model file and reset the entry to [DownloadState.NotDownloaded].
 * Automatically unloads the model first if it is currently in memory.
 */
class DeleteLocalModelUseCase @Inject constructor(
    private val repo: ModelRepository,
) {
    suspend operator fun invoke(modelId: String) {
        repo.unloadModel(modelId)
        repo.deleteLocalFile(modelId)
    }
}

/**
 * Add a user-supplied GGUF / .task model via a custom download URL.
 * Inserts a DB row as a custom entry then starts the download immediately.
 *
 * @return The newly created [ModelEntry] (in [DownloadState.Downloading] state).
 */
class ImportCustomModelUseCase @Inject constructor(
    private val repo: ModelRepository,
) {
    suspend operator fun invoke(
        name:        String,
        downloadUrl: String,
        backend:     ModelBackend,
    ): ModelEntry {
        val entry = repo.importCustomModel(name, downloadUrl, backend)
        repo.startDownload(entry.id)
        return entry
    }
}

// ── Inference ─────────────────────────────────────────────────────────────────

/**
 * Load a model into GPU/RAM via the appropriate backend.
 * Emits status strings as the load progresses ("Loading weights…", etc.).
 * Collecting the flow is required to drive the load — it is not fire-and-forget.
 *
 * Only one model can be in the [DownloadState.Loaded] state at a time;
 * loading a second model automatically unloads the first.
 */
class LoadModelUseCase @Inject constructor(
    private val repo: ModelRepository,
) {
    operator fun invoke(modelId: String): Flow<String> = repo.loadModel(modelId)
}

/**
 * Unload the model from memory and free GPU/RAM.
 * Safe to call even if the model is not currently loaded.
 */
class UnloadModelUseCase @Inject constructor(
    private val repo: ModelRepository,
) {
    suspend operator fun invoke(modelId: String) = repo.unloadModel(modelId)
}

/**
 * Observe the ID of the model currently loaded in memory.
 * Emits null when no model is loaded.
 */
class ObserveLoadedModelUseCase @Inject constructor(
    private val repo: ModelRepository,
) {
    operator fun invoke(): StateFlow<String?> = repo.observeLoadedModelId()
}

/**
 * Stream a generation response from the local model.
 * The [modelId] model must already be loaded via [LoadModelUseCase].
 *
 * The returned [Flow] emits UTF-8 token pieces as they are produced.
 * Cancel the flow's coroutine to stop generation at the next token boundary.
 *
 * @throws IllegalStateException if [modelId] is not loaded.
 */
class GenerateLocalUseCase @Inject constructor(
    private val repo: ModelRepository,
) {
    operator fun invoke(
        modelId: String,
        prompt:  String,
        config:  GenerationConfig = GenerationConfig(),
    ): Flow<String> = repo.generate(modelId, prompt, config)
}

/**
 * Stop any in-progress generation for [modelId].
 * Non-blocking — returns immediately; the token stream closes on its own.
 */
class StopGenerationUseCase @Inject constructor(
    private val repo: ModelRepository,
) {
    operator fun invoke(modelId: String) = repo.stopGeneration(modelId)
}

// ── Benchmark ─────────────────────────────────────────────────────────────────

/**
 * Run the standard JARVIS benchmark suite against a loaded model:
 *   - TTFT (Time to First Token)
 *   - TPS  (Tokens Per Second) over a 200-token run
 *   - Peak RAM and CPU during inference
 *   - GPU layer count active
 *
 * The model must be loaded first. Results are persisted for comparison in the
 * Benchmark tab.
 */
class BenchmarkModelUseCase @Inject constructor(
    private val repo: ModelRepository,
) {
    suspend operator fun invoke(modelId: String): BenchmarkResult =
        repo.benchmark(modelId)
}

/**
 * Retrieve past benchmark results, newest first.
 * Used to populate the comparison bar charts in the Benchmark tab.
 */
class GetBenchmarkHistoryUseCase @Inject constructor(
    private val repo: ModelRepository,
) {
    suspend operator fun invoke(): List<BenchmarkResult> =
        repo.getBenchmarkHistory()
}

// ── Routing ───────────────────────────────────────────────────────────────────

/**
 * Observe the current [RoutingMode] (AUTO / LOCAL / CLOUD / HYBRID).
 * Surfaced as the mode selector chip in the chat input bar.
 */
class ObserveRoutingModeUseCase @Inject constructor(
    private val repo: ModelRepository,
) {
    operator fun invoke(): StateFlow<RoutingMode> = repo.observeRoutingMode()
}

/**
 * Persist a new [RoutingMode] and notify [IntelliRouter] immediately.
 */
class SetRoutingModeUseCase @Inject constructor(
    private val repo: ModelRepository,
) {
    suspend operator fun invoke(mode: RoutingMode) = repo.setRoutingMode(mode)
}

// ── Storage ───────────────────────────────────────────────────────────────────

/**
 * Return the total bytes consumed by all downloaded models on device storage.
 * Shown in the storage bar at the bottom of the Models tab.
 */
class GetModelStorageUseCase @Inject constructor(
    private val repo: ModelRepository,
) {
    suspend operator fun invoke(): Long = repo.getTotalStorageUsed()
}
