package com.jarvis.android.presentation.localai.settings

import android.content.Context
import android.content.SharedPreferences
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.android.system.llm.OllamaBridge
import com.jarvis.android.system.llm.OllamaBridgeConfig
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

// ── UI State ──────────────────────────────────────────────────────────────────

enum class OllamaTestStatus { IDLE, TESTING, OK, FAIL }

data class LocalAiSettingsUiState(
    val gpuLayers:    Int              = 99,
    val contextSize:  Int              = 4096,
    val nThreads:     Int              = 6,
    val ollamaUrl:    String           = "http://10.10.0.50:11434",
    val ollamaToken:  String           = "",
    val ollamaTestStatus: OllamaTestStatus = OllamaTestStatus.IDLE,
    val isSaved:      Boolean          = false,
    val isDirty:      Boolean          = false,
)

// ── ViewModel ─────────────────────────────────────────────────────────────────

@HiltViewModel
class LocalAiSettingsViewModel @Inject constructor(
    @ApplicationContext private val context: Context,
    private val ollamaBridge: OllamaBridge,
) : ViewModel() {

    private val prefs: SharedPreferences =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    private val _uiState = MutableStateFlow(loadFromPrefs())
    val uiState: StateFlow<LocalAiSettingsUiState> = _uiState.asStateFlow()

    // ── Intents ───────────────────────────────────────────────────────────────

    fun onGpuLayersChange(value: Int) =
        _uiState.update { it.copy(gpuLayers = value.coerceIn(0, 100), isDirty = true, isSaved = false) }

    fun onContextSizeChange(value: Int) =
        _uiState.update { it.copy(contextSize = value.coerceIn(512, 131_072), isDirty = true, isSaved = false) }

    fun onThreadsChange(value: Int) =
        _uiState.update { it.copy(nThreads = value.coerceIn(1, 16), isDirty = true, isSaved = false) }

    fun onOllamaUrlChange(value: String) =
        _uiState.update { it.copy(ollamaUrl = value, isDirty = true, isSaved = false, ollamaTestStatus = OllamaTestStatus.IDLE) }

    fun onOllamaTokenChange(value: String) =
        _uiState.update { it.copy(ollamaToken = value, isDirty = true, isSaved = false, ollamaTestStatus = OllamaTestStatus.IDLE) }

    fun onUseLocalhost() {
        _uiState.update { it.copy(ollamaUrl = "http://localhost:11434", isDirty = true, isSaved = false, ollamaTestStatus = OllamaTestStatus.IDLE) }
    }

    fun onTestConnection() = viewModelScope.launch {
        _uiState.update { it.copy(ollamaTestStatus = OllamaTestStatus.TESTING) }
        val state = _uiState.value
        ollamaBridge.configure(OllamaBridgeConfig(
            baseUrl   = state.ollamaUrl,
            modelName = ollamaBridge.config.modelName,
            authToken = state.ollamaToken.ifBlank { null },
        ))
        val reachable = ollamaBridge.isServerReachable()
        _uiState.update { it.copy(ollamaTestStatus = if (reachable) OllamaTestStatus.OK else OllamaTestStatus.FAIL) }
    }

    fun onSave() = viewModelScope.launch {
        val state = _uiState.value
        prefs.edit()
            .putInt(KEY_GPU_LAYERS,   state.gpuLayers)
            .putInt(KEY_CONTEXT_SIZE, state.contextSize)
            .putInt(KEY_THREADS,      state.nThreads)
            .putString(KEY_OLLAMA_URL,   state.ollamaUrl)
            .putString(KEY_OLLAMA_TOKEN, state.ollamaToken)
            .apply()
        _uiState.update { it.copy(isSaved = true, isDirty = false) }
    }

    fun onDismissSaved() = _uiState.update { it.copy(isSaved = false) }

    // ── Prefs load ────────────────────────────────────────────────────────────

    private fun loadFromPrefs() = LocalAiSettingsUiState(
        gpuLayers   = prefs.getInt(KEY_GPU_LAYERS,   99),
        contextSize = prefs.getInt(KEY_CONTEXT_SIZE, 4096),
        nThreads    = prefs.getInt(KEY_THREADS,      6),
        ollamaUrl   = prefs.getString(KEY_OLLAMA_URL,   DEFAULT_OLLAMA_URL)   ?: DEFAULT_OLLAMA_URL,
        ollamaToken = prefs.getString(KEY_OLLAMA_TOKEN, "")                   ?: "",
    )

    companion object {
        private const val PREFS_NAME         = "jarvis_llm_prefs"
        private const val KEY_GPU_LAYERS     = "gpu_layers"
        private const val KEY_CONTEXT_SIZE   = "context_size"
        private const val KEY_THREADS        = "n_threads"
        private const val KEY_OLLAMA_URL     = "ollama_base_url"
        private const val KEY_OLLAMA_TOKEN   = "ollama_token"
        private const val DEFAULT_OLLAMA_URL = "http://10.10.0.50:11434"
    }
}
