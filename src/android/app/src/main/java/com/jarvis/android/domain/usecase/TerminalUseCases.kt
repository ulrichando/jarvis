package com.jarvis.android.domain.usecase

import com.jarvis.android.domain.repository.TerminalRepository
import com.jarvis.android.system.terminal.ActiveSession
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.StateFlow
import javax.inject.Inject

class CreateTerminalSessionUseCase @Inject constructor(private val repo: TerminalRepository) {
    suspend operator fun invoke(name: String = "", asRoot: Boolean = false): ActiveSession? =
        repo.createSession(name, asRoot)
}

class WriteToTerminalUseCase @Inject constructor(private val repo: TerminalRepository) {
    operator fun invoke(sessionId: String, text: String) = repo.write(sessionId, text)
}

class KillTerminalSessionUseCase @Inject constructor(private val repo: TerminalRepository) {
    suspend operator fun invoke(sessionId: String) = repo.killSession(sessionId)
}

class ObserveTerminalSessionsUseCase @Inject constructor(private val repo: TerminalRepository) {
    operator fun invoke(): StateFlow<List<ActiveSession>> = repo.observeSessions()
}

class SearchCommandHistoryUseCase @Inject constructor(private val repo: TerminalRepository) {
    suspend operator fun invoke(prefix: String): List<String> = repo.searchCommands(prefix)
}

class ObserveCommandHistoryUseCase @Inject constructor(private val repo: TerminalRepository) {
    operator fun invoke(sessionId: String): Flow<List<String>> = repo.observeCommandHistory(sessionId)
}
