package com.jarvis.android.presentation.localai.inference

import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.android.domain.model.ModelEntry
import com.jarvis.android.domain.usecase.llm.GenerateLocalUseCase
import com.jarvis.android.domain.usecase.llm.LoadModelUseCase
import com.jarvis.android.domain.usecase.llm.ObserveDownloadedModelsUseCase
import com.jarvis.android.domain.usecase.llm.ObserveLoadedModelUseCase
import com.jarvis.android.domain.usecase.llm.StopGenerationUseCase
import com.jarvis.android.system.llm.GenerationConfig
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Job
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

data class InferenceUiState(
    val downloadedModels: List<ModelEntry> = emptyList(),
    val loadedModelId:    String?          = null,
    val selectedModelId:  String?          = null,
    val isLoading:        Boolean          = false,
    val loadStatus:       String           = "",
    val inputText:        String           = "",
    val response:         String           = "",
    val isGenerating:     Boolean          = false,
    val toast:            String?          = null,
) {
    val loadedModel: ModelEntry?
        get() = downloadedModels.find { it.id == loadedModelId }
    val selectedModel: ModelEntry?
        get() = downloadedModels.find { it.id == (selectedModelId ?: loadedModelId) }
    val canGenerate: Boolean
        get() = loadedModelId != null && inputText.isNotBlank() && !isGenerating
    val canLoad: Boolean
        get() = selectedModelId != null && selectedModelId != loadedModelId && !isLoading
}

// ── ViewModel ─────────────────────────────────────────────────────────────────

@HiltViewModel
class InferenceViewModel @Inject constructor(
    private val observeDownloaded: ObserveDownloadedModelsUseCase,
    private val observeLoadedModel: ObserveLoadedModelUseCase,
    private val loadModel:          LoadModelUseCase,
    private val generateLocal:      GenerateLocalUseCase,
    private val stopGeneration:     StopGenerationUseCase,
) : ViewModel() {

    private val _uiState  = MutableStateFlow(InferenceUiState())
    val uiState: StateFlow<InferenceUiState> = _uiState.asStateFlow()

    private var generateJob: Job? = null

    init {
        observeDownloaded()
            .onEach  { list ->
                _uiState.update { state ->
                    val autoSelect = if (state.selectedModelId == null) list.firstOrNull()?.id else state.selectedModelId
                    state.copy(downloadedModels = list, selectedModelId = autoSelect)
                }
            }
            .catch   { e -> Log.e(TAG, "downloaded flow error", e) }
            .launchIn(viewModelScope)

        observeLoadedModel()
            .onEach  { id -> _uiState.update { it.copy(loadedModelId = id) } }
            .launchIn(viewModelScope)
    }

    // ── Intents ───────────────────────────────────────────────────────────────

    fun onSelectModel(modelId: String) = _uiState.update { it.copy(selectedModelId = modelId) }

    fun onInputChange(text: String) = _uiState.update { it.copy(inputText = text) }

    fun onLoad() = viewModelScope.launch {
        val modelId = _uiState.value.selectedModelId ?: return@launch
        _uiState.update { it.copy(isLoading = true, loadStatus = "Preparing…") }
        loadModel(modelId)
            .onEach  { status -> _uiState.update { it.copy(loadStatus = status) } }
            .catch   { e ->
                Log.e(TAG, "load error", e)
                _uiState.update { it.copy(isLoading = false, loadStatus = "") }
                showToast("Load failed: ${e.message}")
            }
            .launchIn(viewModelScope)
            .join()
        _uiState.update { it.copy(isLoading = false, loadStatus = "") }
    }

    fun onGenerate() {
        val state   = _uiState.value
        val modelId = state.loadedModelId ?: return
        val prompt  = state.inputText.trim().ifBlank { return }

        generateJob?.cancel()
        _uiState.update { it.copy(response = "", isGenerating = true) }

        generateJob = generateLocal(
            modelId = modelId,
            prompt  = prompt,
            config  = GenerationConfig(maxNewTokens = 512, temperature = 0.7f, topK = 40),
        )
            .onEach  { token -> _uiState.update { it.copy(response = it.response + token) } }
            .catch   { e ->
                Log.e(TAG, "generation error", e)
                _uiState.update { it.copy(isGenerating = false) }
                showToast("Generation failed: ${e.message}")
            }
            .launchIn(viewModelScope)

        viewModelScope.launch {
            generateJob?.join()
            _uiState.update { it.copy(isGenerating = false) }
        }
    }

    fun onStop() {
        val modelId = _uiState.value.loadedModelId ?: return
        stopGeneration(modelId)
        generateJob?.cancel()
        _uiState.update { it.copy(isGenerating = false) }
    }

    fun onClearResponse() = _uiState.update { it.copy(response = "", inputText = "") }

    fun onToastShown() = _uiState.update { it.copy(toast = null) }

    private fun showToast(message: String) = _uiState.update { it.copy(toast = message) }

    companion object { private const val TAG = "InferenceViewModel" }
}
