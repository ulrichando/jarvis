package com.jarvis.android.presentation.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.android.data.api.BrainApiService
import com.jarvis.android.data.api.BrainProvider
import com.jarvis.android.data.repository.ApiKeyProviderImpl
import com.jarvis.android.domain.model.CloudModel
import com.jarvis.android.domain.model.CloudProvider
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
    // Optional remote TTS endpoint on the brain server. When set, voice mode
    // streams audio from `<brainTtsUrl>/tts` instead of using Android's
    // local TextToSpeech — same Groq-backed voice as the user's computer.
    val brainTtsUrl:     String  = "",
    /** Active Edge TTS voice — "en-GB-RyanNeural" etc. See [EdgeTtsVoice]. */
    val edgeTtsVoice:    String  = ApiKeyProviderImpl.DEFAULT_EDGE_VOICE,
    /**
     * Whether Edge TTS (online) is enabled. Off by default — local Android
     * TTS is the reliable path, Edge is opt-in.
     */
    val edgeTtsEnabled:  Boolean = false,
    /** Active Groq PlayAI TTS voice — "Fritz-PlayAI" etc. See [GroqTtsVoice]. */
    val groqTtsVoice:    String  = ApiKeyProviderImpl.DEFAULT_GROQ_VOICE,
    /** Whether Groq TTS is enabled. On by default when a Groq key is set. */
    val groqTtsEnabled:  Boolean = true,
    /** True if a Groq API key is configured — gates the Groq TTS section. */
    val hasGroqKey:      Boolean = false,
    // Brain provider selection
    val brainProviders:      List<BrainProvider> = emptyList(),
    val brainPinnedProvider: String              = "",
    val isLoadingProviders:  Boolean             = false,
    // HuggingFace token (for gated model downloads — Gemma, Llama, etc.)
    val hfToken:         String  = "",
    val hfTokenMasked:   String  = "",
    val hasHfToken:      Boolean = false,
    // Direct-cloud multi-provider. Lives under `connectionMode = "anthropic"`
    // (which the UI labels "Direct Cloud" when anything other than Anthropic
    // is selected). Only Anthropic stays on its native Claude path; every
    // other provider hits OpenAiCompatApiService with these settings.
    val directProvider:     CloudProvider = CloudProvider.ANTHROPIC,
    val directModel:        String        = "",
    val directProviderKey:  String        = "",          // in-progress edit value
    val directKeyMasked:    String        = "",          // shown when a key is stored
    val hasDirectKey:       Boolean       = false,
    val isSaving:        Boolean = false,
    val savedMessage:    String? = null,
    val error:           String? = null,
)

sealed class SettingsIntent {
    data class SetApiKey(val key: String)              : SettingsIntent()
    data class SetEndpoint(val endpoint: String)       : SettingsIntent()
    data class SetConnectionMode(val mode: String)     : SettingsIntent()
    data class SetBrainServerUrl(val url: String)      : SettingsIntent()
    data class SetBrainTtsUrl(val url: String)         : SettingsIntent()
    object SaveBrainTtsUrl                             : SettingsIntent()
    data class SelectEdgeTtsVoice(val voiceId: String) : SettingsIntent()
    data class SetEdgeTtsEnabled(val enabled: Boolean) : SettingsIntent()
    data class SelectGroqTtsVoice(val voiceId: String) : SettingsIntent()
    data class SetGroqTtsEnabled(val enabled: Boolean) : SettingsIntent()
    data class PreviewGroqVoice(val voiceId: String)   : SettingsIntent()
    data class PreviewEdgeVoice(val voiceId: String)   : SettingsIntent()
    data class PinBrainProvider(val name: String)      : SettingsIntent()
    data class SetHfToken(val token: String)           : SettingsIntent()
    object SaveApiKey                                  : SettingsIntent()
    object ClearApiKey                                 : SettingsIntent()
    object SaveBrainSettings                           : SettingsIntent()
    object RefreshBrainProviders                       : SettingsIntent()
    object SaveHfToken                                 : SettingsIntent()
    object ClearHfToken                                : SettingsIntent()
    // Direct-cloud provider dropdown
    data class SelectDirectProvider(val provider: CloudProvider) : SettingsIntent()
    data class SelectDirectModel(val modelId: String)            : SettingsIntent()
    data class SetDirectProviderKey(val key: String)             : SettingsIntent()
    object SaveDirectProviderKey                                 : SettingsIntent()
    object ClearDirectProviderKey                                : SettingsIntent()
    object DismissMessage                              : SettingsIntent()
}

@HiltViewModel
class SettingsViewModel @Inject constructor(
    private val apiKeyProvider: ApiKeyProviderImpl,
    private val ttsEngine:      com.jarvis.android.presentation.chat.JarvisTtsEngine,
    private val brainApi:       BrainApiService,
) : ViewModel() {

    private val _uiState = MutableStateFlow(
        run {
            val currentDirect = apiKeyProvider.directProvider
            val currentKey = apiKeyProvider.getProviderKey(currentDirect)
            val storedModel = apiKeyProvider.getDirectModel(currentDirect)
            val defaultModel = CloudModel.CATALOG.firstOrNull { it.provider == currentDirect }?.id.orEmpty()
            SettingsUiState(
                hasApiKey      = apiKeyProvider.hasApiKey(),
                apiKeyMasked   = maskedKey(apiKeyProvider.getApiKey()),
                activeEndpoint = apiKeyProvider.activeEndpoint,
                connectionMode = apiKeyProvider.connectionMode,
                brainServerUrl = apiKeyProvider.getBrainServerUrl(),
                brainTtsUrl    = apiKeyProvider.getBrainTtsUrl(),
                edgeTtsVoice   = apiKeyProvider.getEdgeTtsVoice(),
                edgeTtsEnabled = apiKeyProvider.isEdgeTtsEnabled(),
                groqTtsVoice   = apiKeyProvider.getGroqTtsVoice(),
                groqTtsEnabled = apiKeyProvider.isGroqTtsEnabled(),
                hasGroqKey     = apiKeyProvider.getProviderKey(CloudProvider.GROQ).isNotBlank(),
                hasHfToken     = apiKeyProvider.getHfToken().isNotBlank(),
                hfTokenMasked  = maskedKey(apiKeyProvider.getHfToken()),
                directProvider = currentDirect,
                directModel    = storedModel.ifBlank { defaultModel },
                directKeyMasked = maskedKey(currentKey),
                hasDirectKey    = currentKey.isNotBlank(),
            )
        }
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
            is SettingsIntent.SetBrainTtsUrl ->
                _uiState.update { it.copy(brainTtsUrl = intent.url) }
            is SettingsIntent.SaveBrainTtsUrl -> {
                apiKeyProvider.saveBrainTtsUrl(_uiState.value.brainTtsUrl)
                _uiState.update { it.copy(savedMessage = "TTS server saved") }
            }
            is SettingsIntent.SelectEdgeTtsVoice -> {
                apiKeyProvider.saveEdgeTtsVoice(intent.voiceId)
                _uiState.update { it.copy(
                    edgeTtsVoice = intent.voiceId,
                    savedMessage = "Voice: ${EdgeTtsVoice.labelFor(intent.voiceId)}"
                ) }
            }
            is SettingsIntent.SetEdgeTtsEnabled -> {
                apiKeyProvider.saveEdgeTtsEnabled(intent.enabled)
                _uiState.update { it.copy(
                    edgeTtsEnabled = intent.enabled,
                    savedMessage = if (intent.enabled)
                        "Edge TTS enabled — using ${EdgeTtsVoice.labelFor(_uiState.value.edgeTtsVoice)}"
                    else
                        "Edge TTS off"
                ) }
            }
            is SettingsIntent.SelectGroqTtsVoice -> {
                apiKeyProvider.saveGroqTtsVoice(intent.voiceId)
                _uiState.update { it.copy(
                    groqTtsVoice = intent.voiceId,
                    savedMessage = "Voice: ${GroqTtsVoice.labelFor(intent.voiceId)}"
                ) }
            }
            is SettingsIntent.SetGroqTtsEnabled -> {
                apiKeyProvider.saveGroqTtsEnabled(intent.enabled)
                _uiState.update { it.copy(
                    groqTtsEnabled = intent.enabled,
                    savedMessage = if (intent.enabled)
                        "Groq TTS enabled — using ${GroqTtsVoice.labelFor(_uiState.value.groqTtsVoice)}"
                    else
                        "Groq TTS off"
                ) }
            }
            is SettingsIntent.PreviewGroqVoice -> viewModelScope.launch {
                _uiState.update { it.copy(savedMessage = "Previewing ${GroqTtsVoice.labelFor(intent.voiceId)}…") }
                val ok = ttsEngine.previewGroq(intent.voiceId)
                _uiState.update { it.copy(
                    savedMessage = if (ok)
                        "Preview: ${GroqTtsVoice.labelFor(intent.voiceId)}"
                    else
                        "Groq preview failed — check your API key / network"
                ) }
            }
            is SettingsIntent.PreviewEdgeVoice -> viewModelScope.launch {
                _uiState.update { it.copy(savedMessage = "Previewing ${EdgeTtsVoice.labelFor(intent.voiceId)}…") }
                val ok = ttsEngine.previewEdge(intent.voiceId)
                _uiState.update { it.copy(
                    savedMessage = if (ok)
                        "Preview: ${EdgeTtsVoice.labelFor(intent.voiceId)}"
                    else
                        "Edge preview failed (Microsoft 403'd — endpoint rotates tokens; enable Groq instead)"
                ) }
            }
            is SettingsIntent.SaveBrainSettings -> saveBrainSettings()
            is SettingsIntent.RefreshBrainProviders -> fetchProviders()
            is SettingsIntent.PinBrainProvider  -> pinProvider(intent.name)
            is SettingsIntent.SaveApiKey    -> saveKey()
            is SettingsIntent.ClearApiKey   -> clearKey()
            is SettingsIntent.SetHfToken    -> _uiState.update { it.copy(hfToken = intent.token) }
            is SettingsIntent.SaveHfToken   -> saveHfToken()
            is SettingsIntent.ClearHfToken  -> clearHfToken()
            is SettingsIntent.SelectDirectProvider -> selectDirectProvider(intent.provider)
            is SettingsIntent.SelectDirectModel    -> selectDirectModel(intent.modelId)
            is SettingsIntent.SetDirectProviderKey -> _uiState.update { it.copy(directProviderKey = intent.key) }
            is SettingsIntent.SaveDirectProviderKey -> saveDirectProviderKey()
            is SettingsIntent.ClearDirectProviderKey -> clearDirectProviderKey()
            is SettingsIntent.DismissMessage -> _uiState.update { it.copy(savedMessage = null, error = null) }
        }
    }

    private fun selectDirectProvider(provider: CloudProvider) {
        apiKeyProvider.directProvider = provider
        val storedKey = apiKeyProvider.getProviderKey(provider)
        val storedModel = apiKeyProvider.getDirectModel(provider)
        val defaultModel = CloudModel.CATALOG.firstOrNull { it.provider == provider }?.id.orEmpty()
        _uiState.update {
            it.copy(
                directProvider    = provider,
                directModel       = storedModel.ifBlank { defaultModel },
                directProviderKey = "",
                directKeyMasked   = maskedKey(storedKey),
                hasDirectKey      = storedKey.isNotBlank(),
            )
        }
        // Brain needs the upstream-provider list fetched from the configured
        // server so the user can pin one (or leave it Auto). Cheap call, only
        // runs when the URL is already saved.
        if (provider == CloudProvider.JARVIS_BRAIN && apiKeyProvider.getBrainServerUrl().isNotBlank()) {
            fetchProviders()
        }
    }

    private fun selectDirectModel(modelId: String) {
        val provider = _uiState.value.directProvider
        apiKeyProvider.saveDirectModel(provider, modelId)
        _uiState.update { it.copy(directModel = modelId) }
    }

    private fun saveDirectProviderKey() {
        val provider = _uiState.value.directProvider
        val key = _uiState.value.directProviderKey.trim()
        if (key.isBlank()) {
            _uiState.update { it.copy(error = "${provider.displayName} key cannot be empty") }
            return
        }
        apiKeyProvider.saveProviderKey(provider, key)
        _uiState.update {
            it.copy(
                directProviderKey = "",
                directKeyMasked   = maskedKey(key),
                hasDirectKey      = true,
                savedMessage      = "${provider.displayName} key saved",
            )
        }
    }

    private fun clearDirectProviderKey() {
        val provider = _uiState.value.directProvider
        apiKeyProvider.clearProviderKey(provider)
        _uiState.update {
            it.copy(
                directProviderKey = "",
                directKeyMasked   = "",
                hasDirectKey      = false,
                savedMessage      = "${provider.displayName} key cleared",
            )
        }
    }

    private fun saveHfToken() {
        val token = _uiState.value.hfToken.trim()
        if (token.isBlank()) {
            _uiState.update { it.copy(error = "HF token cannot be empty") }
            return
        }
        apiKeyProvider.saveHfToken(token)
        _uiState.update {
            it.copy(
                hfToken       = "",
                hfTokenMasked = maskedKey(token),
                hasHfToken    = true,
                savedMessage  = "HF token saved",
            )
        }
    }

    private fun clearHfToken() {
        apiKeyProvider.clearHfToken()
        _uiState.update {
            it.copy(
                hfToken       = "",
                hfTokenMasked = "",
                hasHfToken    = false,
                savedMessage  = "HF token cleared",
            )
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
