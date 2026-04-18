package com.jarvis.android.presentation.localai.models

import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.android.domain.model.DownloadState
import com.jarvis.android.domain.model.ModelBackend
import com.jarvis.android.domain.model.ModelEntry
import com.jarvis.android.domain.usecase.llm.CancelDownloadUseCase
import com.jarvis.android.domain.usecase.llm.DeleteLocalModelUseCase
import com.jarvis.android.domain.usecase.llm.DownloadModelUseCase
import com.jarvis.android.domain.usecase.llm.GetModelStorageUseCase
import com.jarvis.android.domain.usecase.llm.ImportCustomModelUseCase
import com.jarvis.android.domain.usecase.llm.LoadModelUseCase
import com.jarvis.android.domain.usecase.llm.ObserveDownloadedModelsUseCase
import com.jarvis.android.domain.usecase.llm.ObserveLoadedModelUseCase
import com.jarvis.android.domain.usecase.llm.ObserveModelsUseCase
import com.jarvis.android.domain.usecase.llm.ObserveRoutingModeUseCase
import com.jarvis.android.domain.usecase.llm.RefreshModelCatalogUseCase
import com.jarvis.android.domain.usecase.llm.SetRoutingModeUseCase
import com.jarvis.android.domain.usecase.llm.UnloadModelUseCase
import com.jarvis.android.domain.model.RoutingMode
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

data class ModelsUiState(
    val models:          List<ModelEntry> = emptyList(),
    val loadedModelId:   String?          = null,
    val routingMode:     RoutingMode      = RoutingMode.AUTO,
    val storageUsedBytes: Long            = 0L,
    val isRefreshing:    Boolean          = false,
    val loadingModelId:  String?          = null,    // model currently being loaded
    val loadProgress:    String           = "",      // status string from loadModel flow
    val showImportDialog: Boolean         = false,
    val toast:           String?          = null,
)

// ── ViewModel ─────────────────────────────────────────────────────────────────

@HiltViewModel
class ModelsViewModel @Inject constructor(
    private val observeModels:       ObserveModelsUseCase,
    private val observeDownloaded:   ObserveDownloadedModelsUseCase,
    private val observeLoadedModel:  ObserveLoadedModelUseCase,
    private val observeRoutingMode:  ObserveRoutingModeUseCase,
    private val refreshCatalog:      RefreshModelCatalogUseCase,
    private val downloadModel:       DownloadModelUseCase,
    private val cancelDownload:      CancelDownloadUseCase,
    private val deleteModel:         DeleteLocalModelUseCase,
    private val loadModel:           LoadModelUseCase,
    private val unloadModel:         UnloadModelUseCase,
    private val importCustom:        ImportCustomModelUseCase,
    private val setRoutingMode:      SetRoutingModeUseCase,
    private val getStorage:          GetModelStorageUseCase,
) : ViewModel() {

    private val _uiState = MutableStateFlow(ModelsUiState())
    val uiState: StateFlow<ModelsUiState> = _uiState.asStateFlow()

    init {
        observeModels()
            .onEach  { list -> _uiState.update { it.copy(models = list) } }
            .catch   { e -> Log.e(TAG, "models flow error", e) }
            .launchIn(viewModelScope)

        observeLoadedModel()
            .onEach  { id -> _uiState.update { it.copy(loadedModelId = id) } }
            .launchIn(viewModelScope)

        observeRoutingMode()
            .onEach  { mode -> _uiState.update { it.copy(routingMode = mode) } }
            .launchIn(viewModelScope)

        viewModelScope.launch {
            try {
                _uiState.update { it.copy(isRefreshing = true) }
                refreshCatalog()
                _uiState.update { it.copy(
                    isRefreshing     = false,
                    storageUsedBytes = getStorage(),
                )}
            } catch (e: Exception) {
                Log.e(TAG, "catalog refresh failed", e)
                _uiState.update { it.copy(isRefreshing = false) }
            }
        }
    }

    // ── Intents ───────────────────────────────────────────────────────────────

    fun onDownload(modelId: String) = viewModelScope.launch {
        try {
            downloadModel(modelId)
        } catch (e: Exception) {
            showToast("Download failed: ${e.message}")
        }
    }

    fun onCancelDownload(modelId: String) = viewModelScope.launch {
        try {
            cancelDownload(modelId)
        } catch (e: Exception) {
            showToast("Cancel failed: ${e.message}")
        }
    }

    fun onDelete(modelId: String) = viewModelScope.launch {
        try {
            deleteModel(modelId)
            _uiState.update { it.copy(storageUsedBytes = getStorage()) }
        } catch (e: Exception) {
            showToast("Delete failed: ${e.message}")
        }
    }

    fun onLoad(modelId: String) = viewModelScope.launch {
        _uiState.update { it.copy(loadingModelId = modelId, loadProgress = "Starting…") }
        try {
            loadModel(modelId)
                .onEach  { status -> _uiState.update { it.copy(loadProgress = status) } }
                .catch   { e ->
                    Log.e(TAG, "load error", e)
                    _uiState.update { it.copy(loadingModelId = null, loadProgress = "") }
                    showToast("Load failed: ${e.message}")
                }
                .launchIn(viewModelScope)
                .join()
            _uiState.update { it.copy(loadingModelId = null, loadProgress = "") }
        } catch (e: Exception) {
            _uiState.update { it.copy(loadingModelId = null, loadProgress = "") }
            showToast("Load failed: ${e.message}")
        }
    }

    fun onUnload(modelId: String) = viewModelScope.launch {
        try {
            unloadModel(modelId)
        } catch (e: Exception) {
            showToast("Unload failed: ${e.message}")
        }
    }

    fun onRoutingModeChange(mode: RoutingMode) = viewModelScope.launch {
        setRoutingMode(mode)
    }

    fun onShowImportDialog() = _uiState.update { it.copy(showImportDialog = true) }
    fun onDismissImportDialog() = _uiState.update { it.copy(showImportDialog = false) }

    fun onImportCustom(name: String, url: String, backend: ModelBackend) = viewModelScope.launch {
        try {
            importCustom(name, url, backend)
            _uiState.update { it.copy(showImportDialog = false) }
            showToast("Importing $name…")
        } catch (e: Exception) {
            showToast("Import failed: ${e.message}")
        }
    }

    fun onToastShown() = _uiState.update { it.copy(toast = null) }

    private fun showToast(message: String) = _uiState.update { it.copy(toast = message) }

    companion object { private const val TAG = "ModelsViewModel" }
}
