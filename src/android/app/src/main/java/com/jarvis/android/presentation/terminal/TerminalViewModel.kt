package com.jarvis.android.presentation.terminal

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.android.domain.usecase.CreateTerminalSessionUseCase
import com.jarvis.android.domain.usecase.KillTerminalSessionUseCase
import com.jarvis.android.domain.usecase.ObserveTerminalSessionsUseCase
import com.jarvis.android.domain.usecase.WriteToTerminalUseCase
import com.jarvis.android.system.terminal.ActiveSession
import com.jarvis.android.system.terminal.TerminalGridSnapshot
import com.jarvis.android.system.terminal.TerminalSessionManager
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.launchIn
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

data class TerminalUiState(
    val sessions:        List<ActiveSession>   = emptyList(),
    val activeSessionId: String?               = null,
    val gridSnapshot:    TerminalGridSnapshot? = null,
    val isCreating:      Boolean               = false,
    /** Non-null when the last new-session attempt failed — surfaced in UI
     *  so the user doesn't see a silent no-op when `+` does nothing. */
    val error:           String?               = null,
)

sealed class TerminalIntent {
    object NewSession : TerminalIntent()
    data class NewRootSession(val name: String = "root") : TerminalIntent()
    data class SelectSession(val id: String) : TerminalIntent()
    data class KillSession(val id: String) : TerminalIntent()
    data class Write(val text: String) : TerminalIntent()
    data class Resize(val rows: Int, val cols: Int) : TerminalIntent()
}

@HiltViewModel
class TerminalViewModel @Inject constructor(
    private val createSession:   CreateTerminalSessionUseCase,
    private val killSession:     KillTerminalSessionUseCase,
    private val observeSessions: ObserveTerminalSessionsUseCase,
    private val writeToTerminal: WriteToTerminalUseCase,
    private val sessionManager:  TerminalSessionManager,
) : ViewModel() {

    private val _uiState = MutableStateFlow(TerminalUiState())
    val uiState: StateFlow<TerminalUiState> = _uiState.asStateFlow()

    private var gridJob: Job? = null

    init {
        observeSessions()
            .onEach { sessions ->
                _uiState.update { it.copy(sessions = sessions) }
                // Auto-select first session if none active
                if (_uiState.value.activeSessionId == null && sessions.isNotEmpty()) {
                    switchTo(sessions.first())
                }
                // Create one session on first launch
                if (sessions.isEmpty() && !_uiState.value.isCreating) {
                    onIntent(TerminalIntent.NewSession)
                }
            }
            .launchIn(viewModelScope)
    }

    fun onIntent(intent: TerminalIntent) {
        when (intent) {
            is TerminalIntent.NewSession -> viewModelScope.launch {
                _uiState.update { it.copy(isCreating = true, error = null) }
                val s = runCatching { createSession(asRoot = false) }.getOrElse { e ->
                    android.util.Log.e("TerminalViewModel", "createSession failed", e)
                    _uiState.update { it.copy(isCreating = false, error = "create failed: ${e.message}") }
                    return@launch
                }
                _uiState.update { it.copy(isCreating = false) }
                if (s != null) switchTo(s)
                else _uiState.update { it.copy(error = "PTY create returned null (max sessions reached or /system/bin/sh missing)") }
            }
            is TerminalIntent.NewRootSession -> viewModelScope.launch {
                _uiState.update { it.copy(isCreating = true, error = null) }
                val s = runCatching { createSession(asRoot = true) }.getOrElse { e ->
                    _uiState.update { it.copy(isCreating = false, error = "root create failed: ${e.message}") }
                    return@launch
                }
                _uiState.update { it.copy(isCreating = false) }
                if (s != null) switchTo(s)
                else _uiState.update { it.copy(error = "PTY root create returned null") }
            }
            is TerminalIntent.SelectSession -> {
                val s = _uiState.value.sessions.firstOrNull { it.id == intent.id }
                s?.let { switchTo(it) }
            }
            is TerminalIntent.KillSession -> viewModelScope.launch {
                killSession(intent.id)
            }
            is TerminalIntent.Write -> {
                val id = _uiState.value.activeSessionId ?: return
                writeToTerminal(id, intent.text)
            }
            is TerminalIntent.Resize -> {
                val id = _uiState.value.activeSessionId ?: return
                sessionManager.resize(id, intent.rows, intent.cols)
            }
        }
    }

    private fun switchTo(session: ActiveSession) {
        _uiState.update { it.copy(activeSessionId = session.id) }
        gridJob?.cancel()
        gridJob = session.gridFlow
            .onEach { snap -> _uiState.update { it.copy(gridSnapshot = snap) } }
            .launchIn(viewModelScope)
    }

    /**
     * Pass-through to the session manager's scrollback fetcher. Called by
     * [com.jarvis.android.presentation.components.TerminalView] when the
     * user drags the canvas upward to reveal history. Returns null if the
     * index is out of range or no active session.
     */
    fun getScrollbackRow(index: Int): ByteArray? {
        val id = _uiState.value.activeSessionId ?: return null
        return sessionManager.getScrollbackRow(id, index)
    }
}
