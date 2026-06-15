package com.jarvis.android.data.repository

import com.jarvis.android.data.local.dao.CommandHistoryDao
import com.jarvis.android.data.local.entity.CommandHistoryEntity
import com.jarvis.android.di.ApplicationScope
import com.jarvis.android.domain.repository.TerminalRepository
import com.jarvis.android.system.terminal.ActiveSession
import com.jarvis.android.system.terminal.TerminalSessionManager
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Thin delegation layer over [TerminalSessionManager] that also writes to
 * [CommandHistoryDao] whenever text ending with `\n` is written to a session.
 */
@Singleton
class TerminalRepositoryImpl @Inject constructor(
    private val sessionManager:    TerminalSessionManager,
    private val commandHistoryDao: CommandHistoryDao,
    @ApplicationScope private val appScope: CoroutineScope,
) : TerminalRepository {

    override fun observeSessions(): StateFlow<List<ActiveSession>> =
        sessionManager.sessions

    override fun getActiveSessionId(): StateFlow<String?> =
        sessionManager.activeSessionId

    override fun setActiveSession(id: String) =
        sessionManager.setActiveSession(id)

    override suspend fun createSession(name: String, asRoot: Boolean): ActiveSession? =
        sessionManager.createSession(name = name, asRoot = asRoot)

    override fun write(sessionId: String, text: String) {
        sessionManager.write(sessionId, text)
        // Record commands that end with newline (i.e. were executed)
        if (text.endsWith("\n")) {
            val command = text.trimEnd()
            if (command.isNotBlank()) {
                recordCommand(sessionId = sessionId, command = command, fromAgent = false)
            }
        }
    }

    override fun resize(sessionId: String, rows: Int, cols: Int) =
        sessionManager.resize(sessionId, rows, cols)

    override suspend fun killSession(sessionId: String) {
        commandHistoryDao.deleteBySession(sessionId)
        sessionManager.killSession(sessionId)
    }

    override fun renameSession(sessionId: String, name: String) =
        sessionManager.renameSession(sessionId, name)

    // ── Command history ───────────────────────────────────────────────────

    override suspend fun getCommandHistory(sessionId: String, limit: Int): List<String> =
        commandHistoryDao.getBySession(sessionId, limit).map { it.command }

    override fun observeCommandHistory(sessionId: String): Flow<List<String>> =
        commandHistoryDao.observeBySession(sessionId).map { list -> list.map { it.command } }

    override suspend fun searchCommands(prefix: String): List<String> =
        commandHistoryDao.searchByPrefix(prefix)

    // ── Internal helpers ──────────────────────────────────────────────────

    private fun recordCommand(sessionId: String, command: String, fromAgent: Boolean) {
        appScope.launch(Dispatchers.IO) {
            commandHistoryDao.insert(
                CommandHistoryEntity(
                    command   = command,
                    sessionId = sessionId,
                    fromAgent = fromAgent,
                )
            )
            commandHistoryDao.pruneOldest()
        }
    }

    /** Called by [JarvisToolDispatcher] when the AI writes to a terminal. */
    suspend fun recordAgentCommand(sessionId: String, command: String) {
        commandHistoryDao.insert(
            CommandHistoryEntity(
                command   = command,
                sessionId = sessionId,
                fromAgent = true,
            )
        )
        commandHistoryDao.pruneOldest()
    }
}
