package com.jarvis.android.presentation.system

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.android.domain.model.AppInfo
import com.jarvis.android.domain.model.ProcessInfo
import com.jarvis.android.domain.model.SystemInfo
import com.jarvis.android.domain.usecase.GetInstalledAppsUseCase
import com.jarvis.android.domain.usecase.GetLogcatUseCase
import com.jarvis.android.domain.usecase.GetProcessesUseCase
import com.jarvis.android.domain.usecase.GetSystemInfoUseCase
import com.jarvis.android.domain.usecase.KillProcessUseCase
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

enum class SystemTab { OVERVIEW, PROCESSES, APPS, LOGCAT }

data class SystemUiState(
    val activeTab:   SystemTab       = SystemTab.OVERVIEW,
    val systemInfo:  SystemInfo?     = null,
    val processes:   List<ProcessInfo> = emptyList(),
    val apps:        List<AppInfo>   = emptyList(),
    val logcat:      List<String>    = emptyList(),
    val logcatTag:   String          = "",
    val logcatLevel: String          = "V",
    val isLoading:   Boolean         = false,
    val error:       String?         = null,
)

sealed class SystemIntent {
    data class SelectTab(val tab: SystemTab) : SystemIntent()
    object Refresh : SystemIntent()
    data class KillProcess(val pid: Int) : SystemIntent()
    data class SetLogcatTag(val tag: String) : SystemIntent()
    data class SetLogcatLevel(val level: String) : SystemIntent()
    object ClearError : SystemIntent()
}

@HiltViewModel
class SystemViewModel @Inject constructor(
    private val getSystemInfo:    GetSystemInfoUseCase,
    private val getProcesses:     GetProcessesUseCase,
    private val killProcess:      KillProcessUseCase,
    private val getInstalledApps: GetInstalledAppsUseCase,
    private val getLogcat:        GetLogcatUseCase,
) : ViewModel() {

    private val _uiState = MutableStateFlow(SystemUiState())
    val uiState: StateFlow<SystemUiState> = _uiState.asStateFlow()

    init {
        loadAll()
        // Auto-refresh system info every 5 seconds
        viewModelScope.launch {
            while (true) {
                delay(5_000)
                if (_uiState.value.activeTab == SystemTab.OVERVIEW ||
                    _uiState.value.activeTab == SystemTab.PROCESSES) {
                    loadSystemInfo()
                    loadProcesses()
                }
            }
        }
    }

    fun onIntent(intent: SystemIntent) {
        when (intent) {
            is SystemIntent.SelectTab      -> {
                _uiState.update { it.copy(activeTab = intent.tab) }
                loadForTab(intent.tab)
            }
            is SystemIntent.Refresh        -> loadAll()
            is SystemIntent.KillProcess    -> handleKill(intent.pid)
            is SystemIntent.SetLogcatTag   -> {
                _uiState.update { it.copy(logcatTag = intent.tag) }
                loadLogcat()
            }
            is SystemIntent.SetLogcatLevel -> {
                _uiState.update { it.copy(logcatLevel = intent.level) }
                loadLogcat()
            }
            is SystemIntent.ClearError     -> _uiState.update { it.copy(error = null) }
        }
    }

    private fun loadAll() {
        loadSystemInfo(); loadProcesses(); loadApps(); loadLogcat()
    }

    private fun loadForTab(tab: SystemTab) = when (tab) {
        SystemTab.OVERVIEW   -> loadSystemInfo()
        SystemTab.PROCESSES  -> loadProcesses()
        SystemTab.APPS       -> loadApps()
        SystemTab.LOGCAT     -> loadLogcat()
    }

    private fun loadSystemInfo() = viewModelScope.launch {
        runCatching { getSystemInfo() }
            .onSuccess { info -> _uiState.update { it.copy(systemInfo = info) } }
            .onFailure { e   -> _uiState.update { it.copy(error = e.message) } }
    }

    private fun loadProcesses() = viewModelScope.launch {
        runCatching { getProcesses(50) }
            .onSuccess { list -> _uiState.update { it.copy(processes = list) } }
    }

    private fun loadApps() = viewModelScope.launch {
        _uiState.update { it.copy(isLoading = true) }
        runCatching { getInstalledApps(userOnly = true) }
            .onSuccess { list -> _uiState.update { it.copy(apps = list, isLoading = false) } }
            .onFailure { e   -> _uiState.update { it.copy(error = e.message, isLoading = false) } }
    }

    private fun loadLogcat() = viewModelScope.launch {
        val state = _uiState.value
        runCatching {
            getLogcat(200, state.logcatTag.ifBlank { null }, state.logcatLevel)
        }.onSuccess { lines -> _uiState.update { it.copy(logcat = lines) } }
         .onFailure { e    -> _uiState.update { it.copy(error = e.message) } }
    }

    private fun handleKill(pid: Int) = viewModelScope.launch {
        killProcess(pid).onFailure { e -> _uiState.update { it.copy(error = e.message) } }
        loadProcesses()
    }
}
