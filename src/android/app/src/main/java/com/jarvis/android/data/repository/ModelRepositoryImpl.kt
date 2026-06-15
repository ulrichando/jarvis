package com.jarvis.android.data.repository

import android.content.Context
import android.content.SharedPreferences
import android.os.Debug
import android.util.Log
import com.jarvis.android.data.local.dao.ModelDao
import com.jarvis.android.data.local.entity.toEntity
import com.jarvis.android.data.local.entity.toDomain
import com.jarvis.android.domain.model.BenchmarkResult
import com.jarvis.android.domain.model.DownloadState
import com.jarvis.android.domain.model.ModelBackend
import com.jarvis.android.domain.model.ModelCapability
import com.jarvis.android.domain.model.ModelEntry
import com.jarvis.android.domain.model.RoutingMode
import com.jarvis.android.domain.repository.ModelRepository
import com.jarvis.android.data.repository.ApiKeyProviderImpl
import com.jarvis.android.system.llm.GenerationConfig
import com.jarvis.android.system.llm.LlamaJNI
import com.jarvis.android.system.llm.LlmLoadConfig
import com.jarvis.android.system.llm.LocalLlmBackend
import com.jarvis.android.system.llm.LiteRtLmBackend
import com.jarvis.android.system.llm.ModelDownloader
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.util.UUID
import javax.inject.Inject
import javax.inject.Singleton
import kotlin.system.measureTimeMillis

/**
 * Concrete implementation of [ModelRepository].
 *
 * ## Backend lifecycle
 *
 * At most one model is loaded at a time. The loaded backend is tracked
 * via [loadedBackend] and the currently loaded model ID in [_loadedModelId].
 * Calling [loadModel] when another model is already loaded automatically
 * unloads it first.
 *
 * ## Backend selection
 *
 *   [ModelBackend.LLAMACPP]    → [LlamaJNI]     (any GGUF, Vulkan GPU)
 *   [ModelBackend.MEDIAPIPE]   → [MediaPipeLLM]  (Gemma 4 .task, GPU delegate)
 *
 * ## Download delegation
 *
 * Downloads are delegated to [ModelDownloaderService]. The repository only
 * manages DB state — it does not perform HTTP itself.
 *
 * ## Routing mode
 *
 * [RoutingMode] is persisted in [SharedPreferences] and exposed as a
 * [StateFlow] so the chat input bar reacts to changes immediately.
 */
@Singleton
class ModelRepositoryImpl @Inject constructor(
    @ApplicationContext private val context: Context,
    private val modelDao:         ModelDao,
    private val llamaJni:         LlamaJNI,
    private val liteRtLm:         LiteRtLmBackend,
    private val downloader:       ModelDownloaderService,
    private val registry:         ModelRegistrySource,
    private val apiKeyProvider:   ApiKeyProviderImpl,
) : ModelRepository {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private val prefs: SharedPreferences =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    // ── Loaded backend tracking ───────────────────────────────────────────────

    @Volatile private var loadedBackend: LocalLlmBackend? = null

    private val _loadedModelId = MutableStateFlow<String?>(null)

    // ── Routing mode ──────────────────────────────────────────────────────────

    private val _routingMode = MutableStateFlow(
        RoutingMode.valueOf(
            prefs.getString(KEY_ROUTING_MODE, RoutingMode.AUTO.name) ?: RoutingMode.AUTO.name
        )
    )

    // ── Benchmark history (in-memory; persisted to DB in a future migration) ──

    private val benchmarkHistory = mutableListOf<BenchmarkResult>()

    // ── ModelRepository impl ──────────────────────────────────────────────────

    // ── Catalog ───────────────────────────────────────────────────────────────

    override fun observeModels(): Flow<List<ModelEntry>> =
        modelDao.observeAll().map { list -> list.map { it.toDomain() } }

    override fun observeDownloaded(): Flow<List<ModelEntry>> =
        modelDao.observeDownloaded().map { list -> list.map { it.toDomain() } }

    override suspend fun getModel(id: String): ModelEntry? =
        modelDao.getById(id)?.toDomain()

    override suspend fun refreshCatalog() {
        // Heal stale DOWNLOADED rows whose file is no longer on disk (user freed
        // space via Files app / Storage manager / adb) before the UI reads state.
        reconcileDownloads()

        // Purge legacy MediaPipe (.task) rows left by older app versions. The
        // current catalog is 100 % GGUF (llama.cpp) + Ollama, but the Room DB
        // persists across APK upgrades so any user who once downloaded a
        // MediaPipe Gemma build still has the row — and hitting "Load" on it
        // would either dispatch to an unsupported backend or, before that
        // route was guarded, SIGSEGV inside libllm_inference_engine_jni.so's
        // drishti thread. Delete the file + reset the row here so the usual
        // deleteStaleNotDownloaded() below can drop the row entirely.
        purgeLegacyMediaPipeRows()

        val domainEntries = registry.getAll()
        val entityEntries = domainEntries.map { it.toEntity() }
        val currentIds    = domainEntries.map { it.id }

        // Remove non-custom rows that are no longer in the catalog and haven't
        // been downloaded — this cleans up old gated or renamed entries from
        // previous app versions so they don't show up in the UI with bad URLs.
        modelDao.deleteStaleNotDownloaded(currentIds)

        // Insert new entries; IGNORE conflict preserves existing download state.
        modelDao.upsertCatalog(entityEntries)

        // Push URL / size / description updates to rows that already existed,
        // so a URL fix in the registry takes effect immediately on next launch.
        domainEntries.forEach { e ->
            modelDao.updateCatalogMetadata(
                id            = e.id,
                name          = e.name,
                description   = e.description,
                downloadUrl   = e.downloadUrl,
                sha256        = e.sha256,
                sizeBytes     = e.sizeBytes,
                ramRequiredMb = e.ramRequiredMb,
            )
        }
        Log.i(TAG, "Catalog refreshed: ${domainEntries.size} entries, purged stale entries")
    }

    /**
     * Heal rows whose download state no longer matches reality. Two failure modes
     * this catches on app start:
     *
     *   1. DOWNLOADED row whose file was deleted outside the app (Files app,
     *      Storage manager, `adb rm`, factory reset of external storage). The UI
     *      otherwise keeps showing "Load" on a corpse and every tap ends in
     *      IllegalStateException deep inside the backend.
     *
     *   2. DOWNLOADING row with no worker alive and no `.tmp` on disk. WorkManager
     *      jobs can die silently (process killed under memory pressure, reboot,
     *      install of a new APK). Without this reset the UI shows a progress bar
     *      forever and the Download button is replaced by Cancel — user is stuck.
     *
     * Ollama rows use an `ollama://` sentinel instead of a real file and are
     * skipped.
     */
    private suspend fun reconcileDownloads() {
        var healed = 0

        // 1) DOWNLOADED → file missing
        modelDao.getDownloaded().forEach { entity ->
            val path = entity.localPath ?: return@forEach
            if (path.startsWith("ollama://")) return@forEach
            if (path.isBlank() || !File(path).exists()) {
                modelDao.markDeleted(entity.id)
                healed++
                Log.w(TAG, "Reconcile: ${entity.id} DOWNLOADED but file missing at $path — reset")
            }
        }

        // 2) DOWNLOADING → worker dead, no partial file
        registry.getAll().forEach { catalogEntry ->
            val row = modelDao.getById(catalogEntry.id) ?: return@forEach
            val state = row.downloadState
            if (!state.startsWith("DOWNLOADING")) return@forEach

            val dest = ModelDownloader.modelFile(context, catalogEntry.id, catalogEntry.downloadUrl)
            val tmp  = File("${dest.absolutePath}.tmp")
            if (!dest.exists() && !tmp.exists()) {
                modelDao.markDeleted(catalogEntry.id)
                healed++
                Log.w(TAG, "Reconcile: ${catalogEntry.id} stuck in $state with no worker — reset")
            }
        }

        if (healed > 0) Log.i(TAG, "Reconcile healed $healed stale download rows")
    }

    /**
     * Delete DB rows whose backend is no longer supported — MediaPipe (.task)
     * and any backend string that isn't a current [ModelBackend] (e.g. the
     * removed OLLAMA / OPENAI_COMPAT local-server entries left by older builds).
     * Paired with [modelDao.deleteStaleNotDownloaded] below: this resets the row
     * to NOT_DOWNLOADED, and the stale-row cleanup then drops it because no
     * catalog entry uses those backends anymore.
     */
    private suspend fun purgeLegacyMediaPipeRows() {
        val downloaded = modelDao.getDownloaded()
        val current = ModelBackend.entries.map { it.name }.toSet()
        var purged = 0
        downloaded.forEach { entity ->
            // entity.backend is persisted as a String in Room, so compare by name.
            val obsolete = entity.backend == ModelBackend.MEDIAPIPE.name ||
                           entity.backend !in current
            if (!obsolete) return@forEach
            entity.localPath?.takeIf { it.isNotBlank() }?.let { path ->
                val file = File(path)
                if (file.exists() && file.delete()) {
                    Log.i(TAG, "Purged legacy model file: $path")
                }
            }
            modelDao.markDeleted(entity.id)
            purged++
        }
        if (purged > 0) Log.i(TAG, "Purged $purged legacy/unsupported backend row(s) from DB")
    }

    // ── Download ──────────────────────────────────────────────────────────────

    override suspend fun startDownload(modelId: String) {
        val entry = modelDao.getById(modelId)?.toDomain()
            ?: error("Model not found: $modelId")

        downloader.enqueue(entry)
        Log.i(TAG, "Download enqueued: $modelId")
    }

    override suspend fun cancelDownload(modelId: String) {
        downloader.cancel(modelId)
        // Reset DB state — the WorkManager job cleans up the partial file
        modelDao.markDeleted(modelId)
    }

    override suspend fun deleteLocalFile(modelId: String) {
        val entity = modelDao.getById(modelId) ?: return
        entity.localPath?.let { path ->
            val file = File(path)
            if (file.exists()) {
                file.delete()
                Log.i(TAG, "Deleted model file: $path")
            }
        }
        modelDao.markDeleted(modelId)
    }

    override suspend fun importCustomModel(
        name:        String,
        downloadUrl: String,
        backend:     ModelBackend,
    ): ModelEntry {
        val id = "custom_${UUID.randomUUID().toString().take(8)}"
        val entry = ModelEntry(
            id           = id,
            name         = name,
            family       = "custom",
            paramCount   = "?",
            quantization = backend.extensions.firstOrNull()?.trimStart('.') ?: "?",
            sizeBytes    = 0L,
            ramRequiredMb = 0,
            backend      = backend,
            downloadUrl  = downloadUrl,
            capabilities = setOf(ModelCapability.CHAT),
            isCustom     = true,
            downloadState = DownloadState.NotDownloaded,
        )
        modelDao.insert(entry.toEntity())
        Log.i(TAG, "Custom model imported: $id — $downloadUrl")
        return entry
    }

    // ── Inference ─────────────────────────────────────────────────────────────

    override fun loadModel(modelId: String): Flow<String> = flow {
        val entity = modelDao.getById(modelId)?.toDomain()
            ?: error("Model not found: $modelId")

        // Unload current model if a different one is loaded
        val current = _loadedModelId.value
        if (current != null && current != modelId) {
            emit("Unloading ${current}…")
            unloadModel(current)
        }

        if (_loadedModelId.value == modelId && loadedBackend?.isLoaded == true) {
            emit("Already loaded")
            return@flow
        }

        val backend = backendFor(entity)

        // Guard against a stale DOWNLOADED row pointing at a file that was deleted
        // outside the app. Reset DB state so the card flips back to Download and
        // surface a specific, user-actionable message instead of a deep backend
        // IllegalStateException. Ollama uses an `ollama://` sentinel, not a file.
        val requiresLocalFile = entity.backend == ModelBackend.MEDIAPIPE ||
                                entity.backend == ModelBackend.LITERTLM  ||
                                entity.backend == ModelBackend.LLAMACPP
        if (requiresLocalFile) {
            val path = entity.localPath
            if (path.isNullOrBlank() || !File(path).exists()) {
                modelDao.markDeleted(modelId)
                Log.w(TAG, "loadModel: $modelId file missing at '$path' — reset to NotDownloaded")
                error("Model file missing — please re-download ${entity.name}.")
            }
        }

        emit("Preparing backend: ${entity.backend.label}…")

        // Read this model's saved ModelConfig (per-model dialog in chat top
        // bar). accelerator + maxTokens come from there; nGpuLayers / nThreads
        // stay on the global prefs slots the llama.cpp path uses.
        val mc = apiKeyProvider.getModelConfig(modelId)
        val cfg = LlmLoadConfig(
            modelPath    = entity.localPath ?: "",
            nGpuLayers   = prefs.getInt(KEY_GPU_LAYERS, DEFAULT_GPU_LAYERS),
            contextSize  = mc.maxTokens.coerceIn(512, entity.contextLength.coerceAtLeast(512)),
            nThreads     = prefs.getInt(KEY_THREADS, DEFAULT_THREADS),
            accelerator  = mc.accelerator.name,   // "GPU" or "CPU"
        )

        emit("Loading ${entity.name}…")
        backend.load(cfg)

        loadedBackend    = backend
        _loadedModelId.value = modelId

        val info = backend.info()
        emit("Loaded: ${info.modelName} (${info.paramCount}, ${info.sizeMb.toInt()} MB)")
        Log.i(TAG, "Model loaded: $modelId via ${entity.backend.label}")
    }.flowOn(Dispatchers.IO)

    override suspend fun unloadModel(modelId: String) {
        if (_loadedModelId.value != modelId) return
        withContext(Dispatchers.IO) {
            loadedBackend?.unload()
        }
        loadedBackend        = null
        _loadedModelId.value = null
        Log.i(TAG, "Model unloaded: $modelId")
    }

    override fun observeLoadedModelId(): StateFlow<String?> =
        _loadedModelId.asStateFlow()

    override fun generate(
        modelId: String,
        prompt:  String,
        config:  GenerationConfig,
    ): Flow<String> {
        val backend = loadedBackend
            ?: error("No model loaded. Load $modelId first.")
        check(_loadedModelId.value == modelId) {
            "Loaded model is ${_loadedModelId.value}, not $modelId"
        }
        return backend.generate(prompt, config)
    }

    override fun stopGeneration(modelId: String) {
        if (_loadedModelId.value == modelId) loadedBackend?.stop()
    }

    // ── Benchmark ─────────────────────────────────────────────────────────────

    /**
     * Runs a standardised 200-token generation, measuring:
     *   - TTFT  (wall clock from first token request to first piece)
     *   - TPS   (tokens / elapsed seconds over the full run)
     *   - Peak RAM (via [Debug.MemoryInfo])
     *   - Peak CPU (sampled once mid-run from /proc/stat)
     */
    override suspend fun benchmark(modelId: String): BenchmarkResult {
        val backend = loadedBackend
            ?: error("Model $modelId not loaded")

        val benchCfg = GenerationConfig(
            maxNewTokens = BENCHMARK_TOKENS,
            temperature  = 0.0f,   // greedy — reproducible
            topK         = 1,
        )
        val prompt = "Explain the concept of neural networks in detail."

        var ttftMs       = 0L
        var totalTokens  = 0
        val startWall    = System.currentTimeMillis()
        var firstToken   = true

        val memInfo = Debug.MemoryInfo()
        var peakRamMb = 0

        withContext(Dispatchers.IO) {
            backend.generate(prompt, benchCfg).collect { _ ->
                totalTokens++
                if (firstToken) {
                    ttftMs     = System.currentTimeMillis() - startWall
                    firstToken = false
                }
                // Sample RAM at the midpoint of the benchmark run
                if (totalTokens == BENCHMARK_TOKENS / 2) {
                    Debug.getMemoryInfo(memInfo)
                    peakRamMb = (memInfo.totalPss / 1_024).toInt()
                }
            }
        }

        val elapsedSec = (System.currentTimeMillis() - startWall) / 1_000f
        val tps = if (elapsedSec > 0f) totalTokens / elapsedSec else 0f

        val result = BenchmarkResult(
            modelId      = modelId,
            modelName    = backend.info().modelName,
            ttftMs       = ttftMs,
            tokensPerSec = tps,
            peakRamMb    = peakRamMb,
            peakCpuPct   = readCpuPercent(),
            gpuLayers    = prefs.getInt(KEY_GPU_LAYERS, DEFAULT_GPU_LAYERS),
            totalTokens  = totalTokens,
        )

        synchronized(benchmarkHistory) {
            benchmarkHistory.add(0, result)
            if (benchmarkHistory.size > MAX_BENCHMARK_HISTORY) {
                benchmarkHistory.removeAt(benchmarkHistory.lastIndex)
            }
        }

        Log.i(TAG, "Benchmark done: ${result.tokensPerSec} TPS, TTFT=${result.ttftMs}ms")
        return result
    }

    override suspend fun getBenchmarkHistory(): List<BenchmarkResult> =
        synchronized(benchmarkHistory) { benchmarkHistory.toList() }

    // ── Routing ───────────────────────────────────────────────────────────────

    override fun observeRoutingMode(): StateFlow<RoutingMode> =
        _routingMode.asStateFlow()

    override suspend fun setRoutingMode(mode: RoutingMode) {
        prefs.edit().putString(KEY_ROUTING_MODE, mode.name).apply()
        _routingMode.value = mode
    }

    // ── Storage ───────────────────────────────────────────────────────────────

    override suspend fun getTotalStorageUsed(): Long =
        modelDao.getTotalDownloadedBytes()

    // ── Private helpers ───────────────────────────────────────────────────────

    private fun backendFor(entry: ModelEntry): LocalLlmBackend = when (entry.backend) {
        // MediaPipe's native LLM engine (libllm_inference_engine_jni.so) segfaults
        // in its `drishti` thread on every Samsung device we've tested — the
        // crash is opaque, deep in Google's native code, and not patchable from
        // the app side (see ModelRegistry.kt top-of-file note). The current
        // catalog has no MEDIAPIPE entries, but a row can still reach this path
        // if a user installed an older build that had a .task model (e.g.
        // gemma3-1b-mediapipe.task) and the Room DB still carries that row.
        // Refuse to dispatch so the process stays alive and the user gets an
        // actionable error instead of SIGSEGV.
        ModelBackend.MEDIAPIPE    -> throw IllegalStateException(
            "MediaPipe (.task) models are no longer supported — Google's native " +
            "engine crashes on Samsung devices. Delete '${entry.name}' from the " +
            "Models screen and pick a LiteRT-LM build instead."
        )
        ModelBackend.LITERTLM     -> liteRtLm
        ModelBackend.LLAMACPP     -> llamaJni
    }

    /** Reads a single-sample CPU utilisation from /proc/stat (best-effort). */
    private fun readCpuPercent(): Int = try {
        val line1 = File("/proc/stat").readLines().firstOrNull() ?: return 0
        Thread.sleep(200)
        val line2 = File("/proc/stat").readLines().firstOrNull() ?: return 0

        fun parseLine(line: String): LongArray {
            val parts = line.trim().split("\\s+".toRegex()).drop(1)
                .take(4).map { it.toLong() }
            return longArrayOf(
                parts[0] + parts[1] + parts[2],  // user + nice + system
                parts[0] + parts[1] + parts[2] + parts[3],  // + idle
            )
        }

        val t1 = parseLine(line1)
        val t2 = parseLine(line2)
        val active = t2[0] - t1[0]
        val total  = t2[1] - t1[1]
        if (total <= 0) 0 else ((active * 100) / total).toInt().coerceIn(0, 100)
    } catch (_: Exception) { 0 }

    // ── Constants ─────────────────────────────────────────────────────────────

    companion object {
        private const val TAG  = "ModelRepository"

        private const val PREFS_NAME  = "jarvis_llm_prefs"
        private const val KEY_ROUTING_MODE  = "routing_mode"
        private const val KEY_GPU_LAYERS    = "gpu_layers"
        private const val KEY_CONTEXT_SIZE  = "context_size"
        private const val KEY_THREADS       = "n_threads"

        private const val DEFAULT_GPU_LAYERS   = 0
        private const val DEFAULT_THREADS      = 4
        private const val BENCHMARK_TOKENS     = 200
        private const val MAX_BENCHMARK_HISTORY = 20
    }
}

// ── Service interfaces (implemented by A7) ────────────────────────────────────

/**
 * Abstracts the WorkManager download engine from the repository.
 * Implemented by [ModelDownloader] (A7).
 */
interface ModelDownloaderService {
    /** Enqueue a resumable background download for [entry]. */
    suspend fun enqueue(entry: ModelEntry)
    /** Cancel the WorkManager job for [modelId] and clean up partial files. */
    suspend fun cancel(modelId: String)
}

/**
 * Provides the built-in model catalog.
 * Implemented by [ModelRegistry] (A7).
 */
interface ModelRegistrySource {
    /** Return all built-in [ModelEntry] items. */
    fun getAll(): List<ModelEntry>
}
