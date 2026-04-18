package com.jarvis.android.presentation.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.android.data.api.BrainApiService
import com.jarvis.android.data.api.BrainProvider
import com.jarvis.android.data.repository.ApiKeyProviderImpl
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

data class SettingsUiState(
    val apiKey:          String  = "",
    val apiKeyMasked:    String  = "",      // shown in UI: "sk-ant-••••••••abcd"
    val hasApiKey:       Boolean = false,
    val activeEndpoint:  String  = "production",
    val endpointOptions: List<String> = listOf("production", "local_relay", "dev"),
    // Brain server connection
    val connectionMode:  String  = "anthropic",   // "anthropic" | "brain"
    val brainServerUrl:  String  = "",
    // Brain provider selection
    val brainProviders:      List<BrainProvider> = emptyList(),
    val brainPinnedProvider: String              = "",
    val isLoadingProviders:  Boolean             = false,
    val isSaving:        Boolean = false,
    val savedMessage:    String? = null,
    val error:           String? = null,
)

sealed class SettingsIntent {
    data class SetApiKey(val key: String)              : SettingsIntent()
    data class SetEndpoint(val endpoint: String)       : SettingsIntent()
    data class SetConnectionMode(val mode: String)     : SettingsIntent()
    data class SetBrainServerUrl(val url: String)      : SettingsIntent()
    data class PinBrainProvider(val name: String)      : SettingsIntent()
    object SaveApiKey                                  : SettingsIntent()
    object ClearApiKey                                 : SettingsIntent()
    object SaveBrainSettings                           : SettingsIntent()
    object RefreshBrainProviders                       : SettingsIntent()
    object DismissMessage                              : SettingsIntent()
}

@HiltViewModel
class SettingsViewModel @Inject constructor(
    private val apiKeyProvider: ApiKeyProviderImpl,
    private val brainApi:       BrainApiService,
) : ViewModel() {

    private val _uiState = MutableStateFlow(
        SettingsUiState(
            hasApiKey      = apiKeyProvider.hasApiKey(),
            apiKeyMasked   = maskedKey(apiKeyProvider.getApiKey()),
            activeEndpoint = apiKeyProvider.activeEndpoint,
            connectionMode = apiKeyProvider.connectionMode,
            brainServerUrl = apiKeyProvider.getBrainServerUrl(),
        )
    )
    val uiState: StateFlow<SettingsUiState> = _uiState.asStateFlow()

    init {
        // If already in brain mode with a saved URL, fetch providers in background
        if (apiKeyProvider.connectionMode == "brain" && apiKeyProvider.getBrainServerUrl().isNotBlank()) {
            fetchProviders()
        }
    }

    fun onIntent(intent: SettingsIntent) {
        when (intent) {
            is SettingsIntent.SetApiKey     -> _uiState.update { it.copy(apiKey = intent.key) }
            is SettingsIntent.SetEndpoint   -> {
                apiKeyProvider.saveEndpoint(intent.endpoint)
                _uiState.update { it.copy(activeEndpoint = intent.endpoint) }
            }
            is SettingsIntent.SetConnectionMode -> {
                apiKeyProvider.saveConnectionMode(intent.mode)
                _uiState.update { it.copy(connectionMode = intent.mode) }
                // Fetch providers immediately when switching to brain mode
                if (intent.mode == "brain" && apiKeyProvider.getBrainServerUrl().isNotBlank()) {
                    fetchProviders()
                }
            }
            is SettingsIntent.SetBrainServerUrl ->
                _uiState.update { it.copy(brainServerUrl = intent.url) }
            is SettingsIntent.SaveBrainSettings -> saveBrainSettings()
            is SettingsIntent.RefreshBrainProviders -> fetchProviders()
            is SettingsIntent.PinBrainProvider  -> pinProvider(intent.name)
            is SettingsIntent.SaveApiKey    -> saveKey()
            is SettingsIntent.ClearApiKey   -> clearKey()
            is SettingsIntent.DismissMessage -> _uiState.update { it.copy(savedMessage = null, error = null) }
        }
    }

    private fun saveKey() {
        val key = _uiState.value.apiKey.trim()
        if (key.isBlank()) {
            _uiState.update { it.copy(error = "API key cannot be empty") }
            return
        }
        viewModelScope.launch {
            _uiState.update { it.copy(isSaving = true) }
            runCatching { apiKeyProvider.saveApiKey(key) }
                .onSuccess {
                    _uiState.update {
                        it.copy(
                            isSaving     = false,
                            apiKey       = "",
                            apiKeyMasked = maskedKey(key),
                            hasApiKey    = true,
                            savedMessage = "API key saved",
                        )
                    }
                }
                .onFailure { e ->
                    _uiState.update { it.copy(isSaving = false, error = e.message) }
                }
        }
    }

    private fun clearKey() {
        apiKeyProvider.clear()
        _uiState.update {
            it.copy(
                apiKey       = "",
                apiKeyMasked = "",
                hasApiKey    = false,
                savedMessage = "API key cleared",
            )
        }
    }

    private fun saveBrainSettings() {
        val url = _uiState.value.brainServerUrl.trim()
        if (url.isBlank()) {
            _uiState.update { it.copy(error = "Brain server URL cannot be empty") }
            return
        }
        apiKeyProvider.saveBrainServerUrl(url)
        _uiState.update { it.copy(savedMessage = "Brain server saved") }
        // Fetch providers from the newly saved URL
        fetchProviders()
    }

    private fun fetchProviders() {
        viewModelScope.launch {
            _uiState.update { it.copy(isLoadingProviders = true) }
            val result = brainApi.listProviders()
            _uiState.update { state ->
                state.copy(
                    brainProviders      = result?.providers?.filter { it.enabled } ?: emptyList(),
                    brainPinnedProvider = result?.pinned ?: "",
                    isLoadingProviders  = false,
                )
            }
        }
    }

    private fun pinProvider(name: String) {
        viewModelScope.launch {
            val ok = brainApi.pinProvider(name)
            if (ok) {
                _uiState.update { it.copy(brainPinnedProvider = name) }
            } else {
                _uiState.update { it.copy(error = "Could not reach brain server") }
            }
        }
    }

    companion object {
        private fun maskedKey(key: String): String {
            if (key.length < 10) return if (key.isBlank()) "" else "••••••"
            return "${key.take(10)}••••••${key.takeLast(4)}"
        }
    }
}
