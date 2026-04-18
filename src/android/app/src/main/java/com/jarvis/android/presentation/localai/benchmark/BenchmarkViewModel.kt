package com.jarvis.android.presentation.localai.benchmark

import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.android.domain.model.BenchmarkResult
import com.jarvis.android.domain.model.ModelEntry
import com.jarvis.android.domain.usecase.llm.BenchmarkModelUseCase
import com.jarvis.android.domain.usecase.llm.GetBenchmarkHistoryUseCase
import com.jarvis.android.domain.usecase.llm.ObserveDownloadedModelsUseCase
import com.jarvis.android.domain.usecase.llm.ObserveLoadedModelUseCase
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.launchIn
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

// ── UI State ──────────────────────────────────────────────────────────────────

data class BenchmarkUiState(
    val downloadedModels: List<ModelEntry>    = emptyList(),
    val loadedModelId:    String?             = null,
    val isRunning:        Boolean             = false,
    val lastResult:       BenchmarkResult?    = null,
    val history:          List<BenchmarkResult> = emptyList(),
    val toast:            String?             = null,
) {
    val loadedModel: ModelEntry?
        get() = downloadedModels.find { it.id == loadedModelId }
    val canRun: Boolean
        get() = loadedModelId != null && !isRunning
}

// ── ViewModel ─────────────────────────────────────────────────────────────────

@HiltViewModel
class BenchmarkViewModel @Inject constructor(
    private val observeDownloaded: ObserveDownloadedModelsUseCase,
    private val observeLoadedModel: ObserveLoadedModelUseCase,
    private val benchmarkModel:     BenchmarkModelUseCase,
    private val getBenchmarkHistory: GetBenchmarkHistoryUseCase,
) : ViewModel() {

    private val _uiState  = MutableStateFlow(BenchmarkUiState())
    val uiState: StateFlow<BenchmarkUiState> = _uiState.asStateFlow()

    init {
        observeDownloaded()
            .onEach  { list -> _uiState.update { it.copy(downloadedModels = list) } }
            .catch   { e -> Log.e(TAG, "downloaded flow error", e) }
            .launchIn(viewModelScope)

        observeLoadedModel()
            .onEach  { id -> _uiState.update { it.copy(loadedModelId = id) } }
            .launchIn(viewModelScope)

        viewModelScope.launch {
            _uiState.update { it.copy(history = getBenchmarkHistory()) }
        }
    }

    fun onRunBenchmark() = viewModelScope.launch {
        val modelId = _uiState.value.loadedModelId ?: return@launch
        _uiState.update { it.copy(isRunning = true, lastResult = null) }
        try {
            val result = benchmarkModel(modelId)
            _uiState.update { state ->
                state.copy(
                    isRunning  = false,
                    lastResult = result,
                    history    = listOf(result) + state.history.take(19),
                )
            }
        } catch (e: Exception) {
            Log.e(TAG, "benchmark error", e)
            _uiState.update { it.copy(isRunning = false) }
            showToast("Benchmark failed: ${e.message}")
        }
    }

    fun onToastShown() = _uiState.update { it.copy(toast = null) }
    private fun showToast(msg: String) = _uiState.update { it.copy(toast = msg) }

    companion object { private const val TAG = "BenchmarkViewModel" }
}
