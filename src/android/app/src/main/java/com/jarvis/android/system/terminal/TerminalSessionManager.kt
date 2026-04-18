package com.jarvis.android.system.terminal

import android.util.Log
import com.jarvis.android.system.root.RootManager
import java.io.File
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlin.coroutines.coroutineContext
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.UUID
import java.util.concurrent.Executors
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Manages up to [PtyManager.MAX_SESSIONS] named terminal sessions (PTY tabs).
 *
 * Each session owns:
 *   - A master PTY file descriptor (from [PtyManager.nativeCreatePty])
 *   - A [VtParser] that maintains the terminal grid
 *   - A reader coroutine that drains the PTY and feeds bytes to [VtParser]
 *   - A [MutableStateFlow] of [TerminalGridSnapshot] that drives [TerminalView]
 *
 * Threading model:
 *   - All PTY I/O and VtParser mutations run on [ptyDispatcher] — a single-thread
 *     Executor — as required by the architecture rules.
 *   - [TerminalGridSnapshot] StateFlows are safe to collect on any coroutine.
 *   - Public methods like [write] and [resize] are safe to call from the UI thread;
 *     they dispatch to [ptyDispatcher] internally.
 *
 * Lifecycle:
 *   - Sessions survive screen rotation (ViewModel holds the manager singleton).
 *   - Sessions survive app backgrounding (JarvisForegroundService holds a reference).
 *   - Sessions are destroyed explicitly via [killSession] or when the app process dies.
 */
@Singleton
class TerminalSessionManager @Inject constructor(
    private val ptyManager: PtyManager,
    private val rootManager: RootManager,
) {

    // ── Single-thread dispatcher for all PTY I/O ──────────────────────────

    /** Architecture rule: all PTY I/O on a dedicated single-thread dispatcher. */
    private val ptyExecutor   = Executors.newSingleThreadExecutor { r ->
        Thread(r, "jarvis-pty").also { it.isDaemon = true }
    }
    val ptyDispatcher = ptyExecutor.asCoroutineDispatcher()

    private val managerScope  = CoroutineScope(SupervisorJob() + ptyDispatcher)

    // ── Session state ─────────────────────────────────────────────────────

    private val _sessions = MutableStateFlow<List<ActiveSession>>(emptyList())
    val sessions: StateFlow<List<ActiveSession>> = _sessions.asStateFlow()

    private val _activeSessionId = MutableStateFlow<String?>(null)
    val activeSessionId: StateFlow<String?> = _activeSessionId.asStateFlow()

    // ── Shell resolution ──────────────────────────────────────────────────

    /**
     * Returns the path to the best available interactive shell, preferring zsh.
     * Checks Termux, system, and fallback paths in order.
     */
    fun resolveShell(): String {
        val candidates = listOf(
            // zsh — Termux (most common on developer/rooted devices)
            "/data/data/com.termux/files/usr/bin/zsh",
            "/data/user/0/com.termux/files/usr/bin/zsh",
            // zsh — system / manual install
            "/system/bin/zsh",
            "/system/xbin/zsh",
            "/data/local/tmp/zsh",
            // bash — Termux / busybox
            "/data/data/com.termux/files/usr/bin/bash",
            "/system/bin/bash",
            "/system/xbin/bash",
        )
        return candidates.firstOrNull { File(it).canExecute() }
            ?: "/system/bin/sh"
    }

    // ── Session creation ──────────────────────────────────────────────────

    /**
     * Create a new terminal session.
     *
     * @param name     Display name shown in the tab bar.
     * @param asRoot   If true and root is available, spawn `su` as the shell.
     * @param rows     Initial row count (updated by [resize]).
     * @param cols     Initial column count (updated by [resize]).
     *
     * Returns the new [ActiveSession], or null if [PtyManager.MAX_SESSIONS] reached
     * or the native PTY allocation fails.
     */
    suspend fun createSession(
        name: String  = "zsh",
        asRoot: Boolean = false,
        rows: Int = 24,
        cols: Int = 80,
    ): ActiveSession? = withContext(ptyDispatcher) {
        if (_sessions.value.size >= PtyManager.MAX_SESSIONS) {
            Log.w(TAG, "createSession: MAX_SESSIONS reached")
            return@withContext null
        }

        val shellPath = resolveShell()
        val shellName = shellPath.substringAfterLast('/')
        val fd = ptyManager.nativeCreatePty(rows, cols, shellPath)
        if (fd < 0) {
            Log.e(TAG, "createSession: nativeCreatePty failed (shell=$shellPath)")
            return@withContext null
        }

        val childPid = ptyManager.nativeGetChildPid(fd)
        val parser   = VtParser(rows, cols)
        val id       = UUID.randomUUID().toString()

        // If root session requested, send "su\n" into the shell immediately
        if (asRoot && rootManager.isRooted) {
            val suCmd = "su\n".toByteArray(Charsets.UTF_8)
            ptyManager.nativeWriteToPty(fd, suCmd, suCmd.size)
        }

        // Use the actual shell binary name as the display name
        val displayName = if (asRoot) "⚡ $shellName" else shellName

        val gridFlow = MutableStateFlow(
            TerminalGridSnapshot(
                grid           = ByteArray(0),
                rows           = rows,
                cols           = cols,
                cursorRow      = 0,
                cursorCol      = 0,
                cursorVisible  = true,
                title          = displayName,
                scrollbackSize = 0,
            )
        )

        val session = ActiveSession(
            id         = id,
            name       = displayName,
            masterFd   = fd,
            childPid   = childPid,
            isRoot     = asRoot && rootManager.isRooted,
            rows       = rows,
            cols       = cols,
            vtParser   = parser,
            gridFlow   = gridFlow,
            readerJob  = Job(),
        )

        _sessions.update { it + session }
        if (_activeSessionId.value == null) _activeSessionId.value = id

        // Start reader coroutine on the PTY dispatcher
        session.readerJob = managerScope.launch {
            runReaderLoop(session)
        }

        Log.i(TAG, "Session created: id=$id fd=$fd pid=$childPid root=${session.isRoot}")
        session
    }

    // ── Reader loop ───────────────────────────────────────────────────────

    /**
     * Reads bytes from the PTY master fd and feeds them to the session's [VtParser].
     * Runs exclusively on [ptyDispatcher]. Exits when the PTY closes (empty array)
     * or the coroutine is cancelled.
     */
    private suspend fun runReaderLoop(session: ActiveSession) {
        val fd     = session.masterFd
        val parser = session.vtParser

        while (coroutineContext.isActive) {
            val bytes = ptyManager.nativeReadFromPty(fd, PtyManager.READ_TIMEOUT_MS)

            when {
                bytes == null -> {
                    // Timeout — no data; loop and try again
                    continue
                }
                bytes.isEmpty() -> {
                    // EOF — shell process has exited
                    Log.i(TAG, "Session ${session.id}: shell exited (EOF)")
                    markSessionDead(session.id)
                    break
                }
                else -> {
                    parser.feed(bytes)
                    emitGridSnapshot(session)
                }
            }
        }
    }

    private fun emitGridSnapshot(session: ActiveSession) {
        val parser   = session.vtParser
        val grid     = parser.getGrid() ?: return
        val (cr, cc) = parser.getCursorPos()

        session.gridFlow.value = TerminalGridSnapshot(
            grid           = grid,
            rows           = parser.rows,
            cols           = parser.cols,
            cursorRow      = cr,
            cursorCol      = cc,
            cursorVisible  = parser.isCursorVisible(),
            title          = parser.getTitle().ifBlank { session.name },
            scrollbackSize = parser.scrollbackSize(),
        )
    }

    // ── Write / resize ────────────────────────────────────────────────────

    /**
     * Write [text] (UTF-8 encoded) into the session's PTY.
     * Safe to call from the UI thread — dispatches to [ptyDispatcher].
     */
    fun write(sessionId: String, text: String) {
        val bytes = text.toByteArray(Charsets.UTF_8)
        write(sessionId, bytes)
    }

    /** Write raw [bytes] into the session's PTY. */
    fun write(sessionId: String, bytes: ByteArray) {
        val session = sessionById(sessionId) ?: return
        managerScope.launch {
            ptyManager.nativeWriteToPty(session.masterFd, bytes, bytes.size)
        }
    }

    /**
     * Notify the PTY and [VtParser] of a new terminal size.
     * Call from [TerminalView]'s `onSizeChanged` callback.
     */
    fun resize(sessionId: String, rows: Int, cols: Int) {
        val session = sessionById(sessionId) ?: return
        managerScope.launch {
            ptyManager.nativeResizePty(session.masterFd, rows, cols)
            session.vtParser.resize(rows, cols)
        }
    }

    // ── Kill session ──────────────────────────────────────────────────────

    /**
     * Kill the session: cancel the reader coroutine, close the PTY fd,
     * free the [VtParser] native handle, and remove from the session list.
     */
    suspend fun killSession(sessionId: String) = withContext(ptyDispatcher) {
        val session = sessionById(sessionId) ?: return@withContext
        session.readerJob.cancel()
        ptyManager.nativeClosePty(session.masterFd)
        session.vtParser.close()

        _sessions.update { list -> list.filter { it.id != sessionId } }

        // If the active session was killed, switch to the previous one
        if (_activeSessionId.value == sessionId) {
            _activeSessionId.value = _sessions.value.lastOrNull()?.id
        }

        Log.i(TAG, "Session killed: $sessionId")
    }

    /** Mark a session as dead without closing the PTY (shell exited on its own). */
    private fun markSessionDead(sessionId: String) {
        _sessions.update { list ->
            list.map { s -> if (s.id == sessionId) s.copy(isAlive = false) else s }
        }
    }

    // ── Navigation ────────────────────────────────────────────────────────

    fun setActiveSession(sessionId: String) {
        if (_sessions.value.any { it.id == sessionId }) {
            _activeSessionId.value = sessionId
        }
    }

    fun renameSession(sessionId: String, name: String) {
        _sessions.update { list ->
            list.map { s -> if (s.id == sessionId) s.copy(name = name) else s }
        }
    }

    fun getSession(sessionId: String): ActiveSession? = sessionById(sessionId)

    // ── Scrollback ────────────────────────────────────────────────────────

    /** Returns a scrollback row for display above the visible grid. */
    fun getScrollbackRow(sessionId: String, index: Int): ByteArray? =
        sessionById(sessionId)?.vtParser?.getScrollbackRow(index)

    // ── Helpers ───────────────────────────────────────────────────────────

    private fun sessionById(id: String): ActiveSession? =
        _sessions.value.firstOrNull { it.id == id }

    /** Kill all sessions. Called from [JarvisForegroundService.onDestroy]. */
    suspend fun killAll() {
        _sessions.value.map { it.id }.forEach { killSession(it) }
        managerScope.cancel()
        ptyExecutor.shutdown()
    }

    private companion object {
        const val TAG = "TerminalSessionManager"
    }
}

// ── Data types ────────────────────────────────────────────────────────────────

/**
 * Live session state held in memory by [TerminalSessionManager].
 * Exposed to [TerminalViewModel] via [TerminalSessionManager.sessions].
 */
data class ActiveSession(
    val id: String,
    val name: String,
    val masterFd: Int,
    val childPid: Int,
    val isRoot: Boolean,
    val rows: Int,
    val cols: Int,
    val vtParser: VtParser,
    val gridFlow: MutableStateFlow<TerminalGridSnapshot>,
    var readerJob: Job,
    val isAlive: Boolean = true,
)

/**
 * Immutable snapshot of the terminal grid, emitted by [ActiveSession.gridFlow]
 * after each [VtParser.feed] call.
 *
 * [TerminalView] collects this flow and triggers a Canvas redraw on each emission.
 *
 * @param grid           Raw cell bytes from [VtParser.getGrid] (13 bytes/cell, row-major).
 * @param rows / cols    Grid dimensions (may change after [TerminalSessionManager.resize]).
 * @param cursorRow/Col  Cursor position for the blinking cursor overlay.
 * @param cursorVisible  False when the shell hides the cursor (e.g. vim insert mode).
 * @param title          OSC 0/2 window title, or the session name as fallback.
 * @param scrollbackSize Number of scrollback lines available above the visible area.
 */
data class TerminalGridSnapshot(
    val grid: ByteArray,
    val rows: Int,
    val cols: Int,
    val cursorRow: Int,
    val cursorCol: Int,
    val cursorVisible: Boolean,
    val title: String,
    val scrollbackSize: Int,
) {
    // ByteArray requires manual equals/hashCode to avoid referential comparison
    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (other !is TerminalGridSnapshot) return false
        return rows == other.rows && cols == other.cols &&
               cursorRow == other.cursorRow && cursorCol == other.cursorCol &&
               cursorVisible == other.cursorVisible && title == other.title &&
               scrollbackSize == other.scrollbackSize &&
               grid.contentEquals(other.grid)
    }

    override fun hashCode(): Int {
        var result = grid.contentHashCode()
        result = 31 * result + rows
        result = 31 * result + cols
        result = 31 * result + cursorRow
        result = 31 * result + cursorCol
        result = 31 * result + cursorVisible.hashCode()
        result = 31 * result + title.hashCode()
        result = 31 * result + scrollbackSize
        return result
    }
}
