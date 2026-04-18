package com.jarvis.android.presentation.chat

import android.content.Context
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.android.domain.model.ChatEvent
import com.jarvis.android.domain.model.RoutingMode
import com.jarvis.android.domain.repository.ModelRepository
import com.jarvis.android.domain.usecase.CreateConversationUseCase
import com.jarvis.android.domain.usecase.DeleteConversationUseCase
import com.jarvis.android.domain.usecase.ObserveConversationsUseCase
import com.jarvis.android.domain.usecase.ObserveMessagesUseCase
import com.jarvis.android.domain.usecase.PinConversationUseCase
import com.jarvis.android.domain.usecase.RenameConversationUseCase
import com.jarvis.android.domain.usecase.SendMessageUseCase
import com.jarvis.android.system.tools.JarvisToolDispatcher
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.launchIn
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.util.Locale
import javax.inject.Inject

@HiltViewModel
class ChatViewModel @Inject constructor(
    @ApplicationContext private val context: Context,
    private val sendMessage:          SendMessageUseCase,
    private val createConversation:   CreateConversationUseCase,
    private val observeConversations: ObserveConversationsUseCase,
    private val observeMessages:      ObserveMessagesUseCase,
    private val deleteConversation:   DeleteConversationUseCase,
    private val renameConversation:   RenameConversationUseCase,
    private val pinConversation:      PinConversationUseCase,
    private val toolDispatcher:       JarvisToolDispatcher,
    private val ttsEngine:            JarvisTtsEngine,
    private val modelRepository:      ModelRepository,
) : ViewModel() {

    private val _uiState = MutableStateFlow(ChatUiState())
    val uiState: StateFlow<ChatUiState> = _uiState.asStateFlow()

    /** Active streaming job — cancelled when the user taps Stop. */
    private var streamJob: Job? = null

    /** Job observing messages for the active conversation. */
    private var messagesJob: Job? = null

    /** Android speech recognizer (lazy-created on first voice tap). */
    private var speechRecognizer: SpeechRecognizer? = null

    init {
        // Observe conversation list
        observeConversations()
            .onEach  { list -> _uiState.update { it.copy(conversations = list) } }
            .catch   { e -> Log.e(TAG, "conversations flow error", e) }
            .launchIn(viewModelScope)

        // Forward tool confirmation requests to UI state
        viewModelScope.launch {
            toolDispatcher.confirmationRequests.collect { request ->
                _uiState.update { it.copy(pendingConfirmation = request) }
            }
        }

        // Observe TTS speaking state → drives sphere animation even after streaming ends
        viewModelScope.launch {
            ttsEngine.isSpeaking.collect { speaking ->
                _uiState.update { it.copy(isTtsSpeaking = speaking) }
            }
        }

        // Observe current routing mode for the input bar label
        viewModelScope.launch {
            modelRepository.observeRoutingMode().collect { mode ->
                _uiState.update { it.copy(routingLabel = mode.label) }
            }
        }
    }

    // ── Intent handler ────────────────────────────────────────────────────────

    fun onIntent(intent: ChatIntent) {
        when (intent) {
            is ChatIntent.SendMessage          -> handleSendMessage(intent.imageBase64)
            is ChatIntent.StopStreaming        -> handleStop()
            is ChatIntent.UpdateInput          -> _uiState.update { it.copy(inputText = intent.text) }
            is ChatIntent.SelectConversation   -> handleSelectConversation(intent.id)
            is ChatIntent.NewConversation      -> handleNewConversation()
            is ChatIntent.DeleteConversation   -> handleDeleteConversation(intent.id)
            is ChatIntent.RenameConversation   -> handleRenameConversation(intent.id, intent.title)
            is ChatIntent.PinConversation      -> handlePinConversation(intent.id, intent.pinned)
            is ChatIntent.ResolveConfirmation  -> handleResolveConfirmation(intent.requestId, intent.allowed)
            is ChatIntent.ClearError           -> _uiState.update { it.copy(error = null) }
            is ChatIntent.ToggleVoice          -> handleToggleVoice()
            is ChatIntent.ToggleTts            -> handleToggleTts()
            is ChatIntent.CycleRoutingMode     -> handleCycleRoutingMode()
        }
    }

    // ── Send message ──────────────────────────────────────────────────────────

    private fun handleSendMessage(imageBase64: String?) {
        val text = _uiState.value.inputText.trim()
        if (text.isBlank() || _uiState.value.isStreaming) return

        _uiState.update { it.copy(inputText = "", isStreaming = true, streamingText = "") }

        streamJob = viewModelScope.launch {
            // Auto-create conversation on first message
            val convId = ensureActiveConversation()

            sendMessage(convId, text, imageBase64)
                .collect { event -> handleChatEvent(event) }
        }
    }

    private fun handleChatEvent(event: ChatEvent) {
        when (event) {
            is ChatEvent.TextDelta -> _uiState.update { s ->
                s.copy(streamingText = s.streamingText + event.text)
            }
            is ChatEvent.ToolCallStarted -> _uiState.update { s ->
                s.copy(
                    activeToolCalls = s.activeToolCalls + ActiveToolCall(
                        id   = event.toolId,
                        name = event.toolName,
                    )
                )
            }
            is ChatEvent.ToolCallCompleted -> _uiState.update { s ->
                s.copy(
                    activeToolCalls = s.activeToolCalls.map { tc ->
                        if (tc.id == event.toolId) tc.copy(
                            isCompleted = true,
                            result      = event.result,
                            isError     = event.isError,
                        ) else tc
                    }
                )
            }
            is ChatEvent.ConfirmationNeeded -> {
                // Already forwarded via toolDispatcher.confirmationRequests flow
            }
            is ChatEvent.TurnSaved -> {
                // Real message is now in the Room flow — clear the ghost bubble
                val finalText = _uiState.value.streamingText
                _uiState.update { it.copy(streamingText = "", activeToolCalls = emptyList()) }
                // Speak the response if TTS is enabled
                if (_uiState.value.ttsEnabled && finalText.isNotBlank()) {
                    ttsEngine.speak(finalText)
                }
            }
            is ChatEvent.Warning -> Log.w(TAG, "Agent warning: ${event.message}")
            is ChatEvent.Error -> _uiState.update { s ->
                s.copy(
                    isStreaming     = false,
                    streamingText   = "",
                    activeToolCalls = emptyList(),
                    error           = event.message,
                )
            }
            is ChatEvent.Done -> _uiState.update { s ->
                s.copy(
                    isStreaming     = false,
                    streamingText   = "",
                    activeToolCalls = emptyList(),
                )
            }
        }
    }

    // ── Stop ──────────────────────────────────────────────────────────────────

    private fun handleStop() {
        streamJob?.cancel()
        streamJob = null
        ttsEngine.stop()
        _uiState.update { it.copy(isStreaming = false, streamingText = "", activeToolCalls = emptyList()) }
    }

    // ── Voice input (STT) ─────────────────────────────────────────────────────

    private fun handleToggleVoice() {
        if (_uiState.value.isRecording) {
            stopListening()
        } else {
            startListening()
        }
    }

    private fun startListening() {
        if (!SpeechRecognizer.isRecognitionAvailable(context)) {
            _uiState.update { it.copy(error = "Speech recognition not available on this device") }
            return
        }

        ttsEngine.stop() // don't record our own TTS output

        val recognizer = speechRecognizer ?: SpeechRecognizer.createSpeechRecognizer(context).also {
            speechRecognizer = it
        }

        recognizer.setRecognitionListener(object : RecognitionListener {
            override fun onReadyForSpeech(params: Bundle?) {
                _uiState.update { it.copy(isRecording = true) }
            }
            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() {
                _uiState.update { it.copy(isRecording = false) }
            }
            override fun onError(error: Int) {
                _uiState.update { it.copy(isRecording = false) }
                Log.w(TAG, "STT error: $error")
            }
            override fun onResults(results: Bundle?) {
                val matches = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                val text = matches?.firstOrNull() ?: return
                _uiState.update { it.copy(inputText = text, isRecording = false) }
            }
            override fun onPartialResults(partial: Bundle?) {
                val matches = partial?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                val text = matches?.firstOrNull() ?: return
                _uiState.update { it.copy(inputText = text) }
            }
            override fun onEvent(eventType: Int, params: Bundle?) {}
        })

        val intent = android.content.Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault())
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
        }
        recognizer.startListening(intent)
    }

    private fun stopListening() {
        speechRecognizer?.stopListening()
        _uiState.update { it.copy(isRecording = false) }
    }

    // ── TTS toggle ────────────────────────────────────────────────────────────

    private fun handleToggleTts() {
        val next = !_uiState.value.ttsEnabled
        ttsEngine.setEnabled(next)
        _uiState.update { it.copy(ttsEnabled = next) }
    }

    // ── Routing mode cycle ────────────────────────────────────────────────────

    private fun handleCycleRoutingMode() {
        viewModelScope.launch {
            val current = modelRepository.observeRoutingMode().value
            val modes = RoutingMode.entries
            val next = modes[(modes.indexOf(current) + 1) % modes.size]
            modelRepository.setRoutingMode(next)
        }
    }

    // ── Conversation management ───────────────────────────────────────────────

    private fun handleSelectConversation(id: String) {
        _uiState.update { it.copy(activeConversationId = id, messages = emptyList()) }
        observeMessagesFor(id)
    }

    private fun handleNewConversation() {
        viewModelScope.launch {
            val conv = createConversation()
            _uiState.update { it.copy(activeConversationId = conv.id, messages = emptyList()) }
            observeMessagesFor(conv.id)
        }
    }

    private fun handleDeleteConversation(id: String) {
        viewModelScope.launch {
            deleteConversation(id)
            if (_uiState.value.activeConversationId == id) {
                val next = _uiState.value.conversations.firstOrNull { it.id != id }
                if (next != null) handleSelectConversation(next.id)
                else _uiState.update { it.copy(activeConversationId = null, messages = emptyList()) }
            }
        }
    }

    private fun handleRenameConversation(id: String, title: String) {
        viewModelScope.launch { renameConversation(id, title) }
    }

    private fun handlePinConversation(id: String, pinned: Boolean) {
        viewModelScope.launch { pinConversation(id, pinned) }
    }

    // ── Tool confirmation ─────────────────────────────────────────────────────

    private fun handleResolveConfirmation(requestId: String, allowed: Boolean) {
        toolDispatcher.resolveConfirmation(requestId, allowed)
        _uiState.update { it.copy(pendingConfirmation = null) }
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private suspend fun ensureActiveConversation(): String {
        val existing = _uiState.value.activeConversationId
        if (existing != null) return existing

        val conv = createConversation()
        _uiState.update { it.copy(activeConversationId = conv.id) }
        observeMessagesFor(conv.id)
        return conv.id
    }

    private fun observeMessagesFor(conversationId: String) {
        messagesJob?.cancel()
        messagesJob = observeMessages(conversationId)
            .onEach  { list -> _uiState.update { it.copy(messages = list) } }
            .catch   { e -> Log.e(TAG, "messages flow error", e) }
            .launchIn(viewModelScope)
    }

    override fun onCleared() {
        super.onCleared()
        speechRecognizer?.destroy()
        speechRecognizer = null
        ttsEngine.shutdown()
    }

    private companion object {
        const val TAG = "ChatViewModel"
    }
}
