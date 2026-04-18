package com.jarvis.android.presentation.cyber

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.android.domain.model.HttpInspectResult
import com.jarvis.android.domain.model.CyberProcess
import com.jarvis.android.domain.model.LogEntry
import com.jarvis.android.domain.model.LogLevel
import com.jarvis.android.domain.model.NetworkSnapshot
import com.jarvis.android.domain.model.PortScanResult
import com.jarvis.android.domain.model.ProcessSnapshot
import com.jarvis.android.domain.model.ScanState
import com.jarvis.android.domain.usecase.cyber.GetNetworkSnapshotUseCase
import com.jarvis.android.domain.usecase.cyber.GetProcessSnapshotUseCase
import com.jarvis.android.domain.usecase.cyber.HttpInspectUseCase
import com.jarvis.android.domain.usecase.cyber.PortScanUseCase
import com.jarvis.android.domain.usecase.cyber.WatchLogsUseCase
import com.jarvis.android.system.cyber.LogWatcher
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

// ── UI state ──────────────────────────────────────────────────────────────────

data class CyberUiState(
    // ── Red Team — port scan ──
    val scanTarget:     String        = "",
    val scanState:      ScanState     = ScanState.IDLE,
    val scanProgress:   Float         = 0f,
    val portScanResult: PortScanResult? = null,

    // ── Red Team — HTTP inspect ──
    val httpTarget:     String           = "",
    val httpState:      ScanState        = ScanState.IDLE,
    val httpResult:     HttpInspectResult? = null,

    // ── Blue Team — processes ──
    val processState:   ScanState        = ScanState.IDLE,
    val processSnap:    ProcessSnapshot? = null,
    val processFilter:  String           = "",

    // ── Blue Team — network ──
    val networkState:   ScanState        = ScanState.IDLE,
    val networkSnap:    NetworkSnapshot? = null,
    val showSuspiciousOnly: Boolean      = false,

    // ── Blue Team — logcat ──
    val logState:       ScanState        = ScanState.IDLE,
    val logEntries:     List<LogEntry>   = emptyList(),
    val logFilter:      String           = "",
    val logMinLevel:    LogLevel         = LogLevel.WARN,

    // ── Common ──
    val errorMessage:   String?          = null,
) {
    val filteredProcesses: List<CyberProcess> get() = processSnap?.processes?.let { list ->
        if (processFilter.isBlank()) list
        else list.filter {
            it.name.contains(processFilter, ignoreCase = true) ||
            it.cmdline.contains(processFilter, ignoreCase = true)
        }
    } ?: emptyList()

    val filteredConnections get() = networkSnap?.connections?.let { list ->
        if (showSuspiciousOnly) list.filter { it.suspicious } else list
    } ?: emptyList()

    val filteredLogs get() = logEntries.let { list ->
        var result = list.filter { it.level.ordinal >= logMinLevel.ordinal }
        if (logFilter.isNotBlank()) {
            result = result.filter {
                it.tag.contains(logFilter, ignoreCase = true) ||
                it.message.contains(logFilter, ignoreCase = true)
            }
        }
        result
    }
}

// ── ViewModel ─────────────────────────────────────────────────────────────────

@HiltViewModel
class CyberViewModel @Inject constructor(
    private val portScan:          PortScanUseCase,
    private val httpInspect:       HttpInspectUseCase,
    private val getProcesses:      GetProcessSnapshotUseCase,
    private val getNetwork:        GetNetworkSnapshotUseCase,
    private val watchLogs:         WatchLogsUseCase,
) : ViewModel() {

    private val _ui = MutableStateFlow(CyberUiState())
    val ui: StateFlow<CyberUiState> = _ui.asStateFlow()

    private var logJob: Job? = null

    // ── Field setters ─────────────────────────────────────────────────────────

    fun setScanTarget(v: String)    { _ui.update { it.copy(scanTarget = v) } }
    fun setHttpTarget(v: String)    { _ui.update { it.copy(httpTarget = v) } }
    fun setProcessFilter(v: String) { _ui.update { it.copy(processFilter = v) } }
    fun setLogFilter(v: String)     { _ui.update { it.copy(logFilter = v) } }
    fun setLogMinLevel(v: LogLevel) { _ui.update { it.copy(logMinLevel = v) } }
    fun toggleSuspiciousOnly()      { _ui.update { it.copy(showSuspiciousOnly = !it.showSuspiciousOnly) } }
    fun clearError()                { _ui.update { it.copy(errorMessage = null) } }

    // ── Red Team — Port Scan ──────────────────────────────────────────────────

    fun startPortScan() {
        val target = _ui.value.scanTarget.trim()
        if (target.isBlank()) return

        viewModelScope.launch {
            _ui.update { it.copy(scanState = ScanState.RUNNING, scanProgress = 0f, portScanResult = null, errorMessage = null) }
            val result = portScan(
                target = target,
                onProgress = { scanned, total ->
                    _ui.update { it.copy(scanProgress = scanned.toFloat() / total.coerceAtLeast(1)) }
                },
            )
            _ui.update {
                it.copy(
                    scanState     = if (result.error != null) ScanState.ERROR else ScanState.DONE,
                    scanProgress  = 1f,
                    portScanResult = result,
                    errorMessage  = result.error,
                )
            }
        }
    }

    // ── Red Team — HTTP Inspect ───────────────────────────────────────────────

    fun startHttpInspect() {
        val url = _ui.value.httpTarget.trim()
        if (url.isBlank()) return

        viewModelScope.launch {
            _ui.update { it.copy(httpState = ScanState.RUNNING, httpResult = null, errorMessage = null) }
            val result = httpInspect(url)
            _ui.update {
                it.copy(
                    httpState  = if (result.error != null) ScanState.ERROR else ScanState.DONE,
                    httpResult = result,
                    errorMessage = result.error,
                )
            }
        }
    }

    // ── Blue Team — Processes ─────────────────────────────────────────────────

    fun refreshProcesses() {
        viewModelScope.launch {
            _ui.update { it.copy(processState = ScanState.RUNNING) }
            try {
                val snap = getProcesses()
                _ui.update { it.copy(processState = ScanState.DONE, processSnap = snap) }
            } catch (e: Exception) {
                _ui.update { it.copy(processState = ScanState.ERROR, errorMessage = e.message) }
            }
        }
    }

    // ── Blue Team — Network ───────────────────────────────────────────────────

    fun refreshNetwork() {
        viewModelScope.launch {
            _ui.update { it.copy(networkState = ScanState.RUNNING) }
            try {
                val snap = getNetwork()
                _ui.update { it.copy(networkState = ScanState.DONE, networkSnap = snap) }
            } catch (e: Exception) {
                _ui.update { it.copy(networkState = ScanState.ERROR, errorMessage = e.message) }
            }
        }
    }

    // ── Blue Team — Logcat ────────────────────────────────────────────────────

    fun startLogWatch() {
        if (logJob?.isActive == true) return
        _ui.update { it.copy(logState = ScanState.RUNNING, logEntries = emptyList()) }

        logJob = viewModelScope.launch {
            watchLogs()
                .catch { e ->
                    _ui.update { it.copy(logState = ScanState.ERROR, errorMessage = e.message) }
                }
                .collect { entry ->
                    _ui.update { state ->
                        val updated = (listOf(entry) + state.logEntries).take(MAX_LOG_ENTRIES)
                        state.copy(logEntries = updated, logState = ScanState.RUNNING)
                    }
                }
        }
    }

    fun stopLogWatch() {
        logJob?.cancel()
        logJob = null
        _ui.update { it.copy(logState = ScanState.IDLE) }
    }

    fun clearLogs() {
        _ui.update { it.copy(logEntries = emptyList()) }
    }

    override fun onCleared() {
        super.onCleared()
        logJob?.cancel()
    }

    companion object {
        private const val MAX_LOG_ENTRIES = 500
    }
}
