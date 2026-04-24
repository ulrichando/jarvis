package com.jarvis.android.domain.repository

import com.jarvis.android.domain.model.AppInfo
import com.jarvis.android.domain.model.BenchmarkResult
import com.jarvis.android.domain.model.ChatEvent
import com.jarvis.android.domain.model.Conversation
import com.jarvis.android.domain.model.FileItem
import com.jarvis.android.domain.model.FileStats
import com.jarvis.android.domain.model.LocationReading
import com.jarvis.android.domain.model.Message
import com.jarvis.android.domain.model.ModelEntry
import com.jarvis.android.domain.model.OrientationReading
import com.jarvis.android.domain.model.ProcessInfo
import com.jarvis.android.domain.model.RoutingMode
import com.jarvis.android.domain.model.SensorInfo
import com.jarvis.android.domain.model.SensorReading
import com.jarvis.android.domain.model.SystemInfo
import com.jarvis.android.system.llm.GenerationConfig
import com.jarvis.android.system.terminal.ActiveSession
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.StateFlow

// ── Chat ──────────────────────────────────────────────────────────────────────

interface ChatRepository {
    fun observeConversations(): Flow<List<Conversation>>
    suspend fun getConversation(id: String): Conversation?
    suspend fun createConversation(title: String, model: String): Conversation
    suspend fun renameConversation(id: String, title: String)
    suspend fun pinConversation(id: String, pinned: Boolean)
    suspend fun deleteConversation(id: String)
    suspend fun deleteAllConversations()

    fun observeMessages(conversationId: String): Flow<List<Message>>
    suspend fun getRecentMessages(conversationId: String, limit: Int = 40): List<Message>

    /**
     * Send [content] in [conversationId] and run the full agent loop.
     *
     * The returned [Flow] emits [ChatEvent]s while the turn is in progress:
     *   - [ChatEvent.TextDelta] for each streamed token
     *   - [ChatEvent.ToolCallStarted] / [ChatEvent.ToolCallCompleted] per tool
     *   - [ChatEvent.ConfirmationNeeded] when a dangerous tool needs approval
     *   - [ChatEvent.TurnSaved] once the assistant message is persisted
     *   - [ChatEvent.Error] on failure
     *   - [ChatEvent.Done] when the loop exits cleanly
     *
     * @param image  Optional base64-encoded JPEG for vision queries.
     */
    /**
     * @param content      Full payload sent to the model, including any
     *                     prepended document context.
     * @param image        Optional base64-encoded JPEG for vision queries.
     * @param displayText  Text to persist in the DB + render in the user
     *                     bubble. Null ⇒ fall back to [content].
     */
    fun sendMessage(
        conversationId: String,
        content:        String,
        image:          String? = null,
        displayText:    String? = null,
    ): Flow<ChatEvent>
}

// ── System ────────────────────────────────────────────────────────────────────

interface SystemRepository {
    suspend fun getSystemInfo(): SystemInfo
    suspend fun getProcesses(limit: Int = 30): List<ProcessInfo>
    suspend fun killProcess(pid: Int, signal: String = "SIGTERM"): Result<Unit>
    suspend fun getInstalledApps(userOnly: Boolean = true): List<AppInfo>
    suspend fun getLogcat(lines: Int = 100, tag: String? = null, level: String = "V"): List<String>
    suspend fun executeCommand(command: String, asRoot: Boolean = false): String
}

// ── File ──────────────────────────────────────────────────────────────────────

interface FileRepository {
    suspend fun listDirectory(path: String, asRoot: Boolean = false): Result<List<FileItem>>
    suspend fun readFile(path: String, maxBytes: Int = 65536, asRoot: Boolean = false): Result<String>
    suspend fun writeFile(path: String, content: String, append: Boolean = false, asRoot: Boolean = false): Result<Unit>
    suspend fun deleteFile(path: String, asRoot: Boolean = false): Result<Unit>
    suspend fun moveFile(from: String, to: String, asRoot: Boolean = false): Result<Unit>
    suspend fun copyFile(from: String, to: String, asRoot: Boolean = false): Result<Unit>
    suspend fun getStats(path: String): Result<FileStats>
    suspend fun createDirectory(path: String, asRoot: Boolean = false): Result<Unit>
}

// ── Terminal ──────────────────────────────────────────────────────────────────

interface TerminalRepository {
    fun observeSessions(): StateFlow<List<ActiveSession>>
    fun getActiveSessionId(): StateFlow<String?>
    fun setActiveSession(id: String)
    suspend fun createSession(name: String = "sh", asRoot: Boolean = false): ActiveSession?
    fun write(sessionId: String, text: String)
    fun resize(sessionId: String, rows: Int, cols: Int)
    suspend fun killSession(sessionId: String)
    fun renameSession(sessionId: String, name: String)
    suspend fun getCommandHistory(sessionId: String, limit: Int = 200): List<String>
    fun observeCommandHistory(sessionId: String): Flow<List<String>>
    suspend fun searchCommands(prefix: String): List<String>
}

// ── Sensor ────────────────────────────────────────────────────────────────────

interface SensorRepository {
    fun getAvailableSensors(): List<SensorInfo>
    fun observeSensor(type: Int, samplingUs: Int = 200_000): Flow<SensorReading>
    fun observeLocation(): Flow<LocationReading>
    fun observeOrientation(): Flow<OrientationReading>
}

// ── Model (Local LLM) ─────────────────────────────────────────────────────────

interface ModelRepository {

    // ── Catalog ───────────────────────────────────────────────────────────────

    /** All catalog + custom entries as a reactive stream. */
    fun observeModels(): Flow<List<ModelEntry>>

    /** Models with a local file present, ready to load. */
    fun observeDownloaded(): Flow<List<ModelEntry>>

    suspend fun getModel(id: String): ModelEntry?

    /**
     * Seed or refresh the built-in catalog from [ModelRegistry].
     * Existing rows with the same id are preserved (download state unchanged).
     */
    suspend fun refreshCatalog()

    // ── Download ──────────────────────────────────────────────────────────────

    /**
     * Enqueue a background WorkManager download for [modelId].
     * Progress is observed via [observeModels] as [DownloadState.Downloading].
     */
    suspend fun startDownload(modelId: String)

    /** Cancel an in-progress download. Partial file is removed. */
    suspend fun cancelDownload(modelId: String)

    /**
     * Delete the local file for [modelId] and reset its state to NotDownloaded.
     * No-op if the model is not currently on device.
     */
    suspend fun deleteLocalFile(modelId: String)

    /**
     * Add a user-supplied model via a direct download URL.
     * The entry is inserted into the DB as a custom row before the download starts.
     */
    suspend fun importCustomModel(
        name:        String,
        downloadUrl: String,
        backend:     com.jarvis.android.domain.model.ModelBackend,
    ): ModelEntry

    // ── Inference ─────────────────────────────────────────────────────────────

    /**
     * Load [modelId] into memory via the appropriate backend.
     * Emits progress strings (e.g. "Loading weights…", "Initialising GPU…").
     */
    fun loadModel(modelId: String): Flow<String>

    /** Unload the currently loaded model and free GPU/RAM. */
    suspend fun unloadModel(modelId: String)

    /** ID of the model currently in memory, or null. */
    fun observeLoadedModelId(): StateFlow<String?>

    /**
     * Stream a local inference response for [prompt].
     * Model must be loaded first via [loadModel].
     */
    fun generate(
        modelId: String,
        prompt:  String,
        config:  GenerationConfig,
    ): Flow<String>

    /** Stop any in-progress generation for [modelId]. */
    fun stopGeneration(modelId: String)

    // ── Benchmark ─────────────────────────────────────────────────────────────

    /**
     * Run the standard benchmark suite on [modelId].
     * Emits [BenchmarkResult] when complete.
     */
    suspend fun benchmark(modelId: String): BenchmarkResult

    /** All past benchmark results, newest first. */
    suspend fun getBenchmarkHistory(): List<BenchmarkResult>

    // ── Routing ───────────────────────────────────────────────────────────────

    fun observeRoutingMode(): StateFlow<RoutingMode>
    suspend fun setRoutingMode(mode: RoutingMode)

    /** Total bytes used by all downloaded models on device storage. */
    suspend fun getTotalStorageUsed(): Long
}
