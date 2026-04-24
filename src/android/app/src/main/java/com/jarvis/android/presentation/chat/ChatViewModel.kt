package com.jarvis.android.presentation.chat

import android.content.Context
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.android.core.network.ApiKeyProvider
import com.jarvis.android.data.repository.ApiKeyProviderImpl
import com.jarvis.android.domain.model.ChatEvent
import com.jarvis.android.domain.model.CloudModel
import com.jarvis.android.domain.model.CloudProvider
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
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.launchIn
import kotlinx.coroutines.flow.map
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
    private val apiKeyProvider:       ApiKeyProvider,
    private val apiKeyProviderImpl:   ApiKeyProviderImpl,
    private val messageDao:           com.jarvis.android.data.local.dao.MessageDao,
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
        // ── Pre-select the user's default model so the chat picker shows
        //     something chosen on cold launch, instead of "no model" until
        //     they tap. directProvider defaults to GROQ after the one-time
        //     migration in ApiKeyProviderImpl; getDirectModel(GROQ) returns
        //     "openai/gpt-oss-120b" unless the user has explicitly saved a
        //     different model. Also flips routing to CLOUD so the first
        //     message goes to the cloud path without requiring a manual pick.
        run {
            val provider = apiKeyProviderImpl.directProvider
            val model    = apiKeyProviderImpl.getDirectModel(provider).ifBlank {
                com.jarvis.android.domain.model.CloudModel.CATALOG
                    .firstOrNull { it.provider == provider }?.id
            }
            if (model != null) {
                _uiState.update { it.copy(selectedCloudModelId = model) }
                viewModelScope.launch {
                    modelRepository.setRoutingMode(
                        com.jarvis.android.domain.model.RoutingMode.CLOUD,
                    )
                }
            }
        }

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
        // Per-word speech tick → drives the voice-mode glow pulse so it
        // beats in sync with the actual TTS audio output.
        viewModelScope.launch {
            ttsEngine.speechTick.collect { tick ->
                _uiState.update { it.copy(ttsSpeechTick = tick) }
            }
        }

        // Observe routing mode + loaded model name + downloaded catalog.
        // The top bar renders the loaded model's display name when routing is
        // LOCAL and something is loaded, and exposes the downloaded models so
        // the home-bar dropdown can list them for one-tap switching.
        viewModelScope.launch {
            kotlinx.coroutines.flow.combine(
                modelRepository.observeRoutingMode(),
                modelRepository.observeLoadedModelId(),
                modelRepository.observeDownloaded(),
                apiKeyProviderImpl.keyChanges,
                // Watch the user's selected model too — tapping a new model
                // in the picker updates _uiState.selectedCloudModelId, which
                // must re-fire this combine so the top-bar label re-renders
                // with the new model's display name.
                _uiState.map { it.selectedCloudModelId }.distinctUntilChanged(),
            ) { args ->
                @Suppress("UNCHECKED_CAST")
                val mode       = args[0] as RoutingMode
                @Suppress("UNCHECKED_CAST")
                val loadedId   = args[1] as String?
                @Suppress("UNCHECKED_CAST")
                val downloaded = args[2] as List<com.jarvis.android.domain.model.ModelEntry>
                val selectedId = args[4] as String?

                val loadedName   = downloaded.firstOrNull { it.id == loadedId }?.name
                val cloudModels  = CloudModel.CATALOG
                    .filter { apiKeyProvider.hasApiKey(it.provider) }
                val cloudSelectedName = cloudModels.firstOrNull {
                    it.id == selectedId
                }?.label
                val label = when {
                    mode == RoutingMode.LOCAL && loadedName != null -> loadedName
                    mode == RoutingMode.LOCAL                       -> "Local · no model"
                    mode == RoutingMode.CLOUD && cloudSelectedName != null ->
                        cloudSelectedName
                    else -> mode.label
                }
                ResolvedTopBar(label, downloaded, loadedId, cloudModels)
            }.collect { snap ->
                _uiState.update {
                    it.copy(
                        routingLabel         = snap.label,
                        downloadedModels     = snap.downloaded,
                        loadedLocalModelId   = snap.loadedId,
                        availableCloudModels = snap.cloudModels,
                    )
                }
            }
        }
    }

    /** Private carrier for the combine() above so we can return >3 values. */
    private data class ResolvedTopBar(
        val label:       String,
        val downloaded:  List<com.jarvis.android.domain.model.ModelEntry>,
        val loadedId:    String?,
        val cloudModels: List<CloudModel>,
    )

    // ── Intent handler ────────────────────────────────────────────────────────

    fun onIntent(intent: ChatIntent) {
        when (intent) {
            is ChatIntent.SendMessage          -> handleSendMessage(intent.imageBase64)
            is ChatIntent.StopStreaming        -> handleStop()
            is ChatIntent.ShowModelConfig      -> {
                // Load the currently active local model's config so the
                // dialog opens populated with the saved values (or defaults
                // if nothing is loaded / the model has never been tuned).
                val modelId = _uiState.value.loadedLocalModelId
                val cfg = if (modelId != null)
                    apiKeyProviderImpl.getModelConfig(modelId)
                else
                    com.jarvis.android.domain.model.ModelConfig()
                _uiState.update {
                    it.copy(showModelConfig = true, editingModelConfig = cfg)
                }
            }
            is ChatIntent.DismissModelConfig   -> _uiState.update {
                it.copy(showModelConfig = false, editingModelConfig = null)
            }
            is ChatIntent.SaveModelConfig      -> {
                apiKeyProviderImpl.saveModelConfig(intent.modelId, intent.config)
                _uiState.update {
                    it.copy(showModelConfig = false, editingModelConfig = null)
                }
                Log.i(TAG, "Saved model config for ${intent.modelId}: ${intent.config}")
            }
            is ChatIntent.UpdateInput          -> _uiState.update { it.copy(inputText = intent.text) }
            is ChatIntent.StageImage           -> _uiState.update {
                it.copy(
                    pendingImageB64         = intent.b64,
                    pendingImageMime        = intent.mime,
                    pendingImagePreviewUri  = intent.previewUri,
                    // An image and a file chip are mutually exclusive — the
                    // user is attaching one thing at a time in this flow.
                    pendingFileName         = null,
                )
            }
            is ChatIntent.StageFile            -> _uiState.update {
                it.copy(
                    pendingFileName         = intent.fileName,
                    pendingFileContent      = intent.content,
                    pendingImageB64         = null,
                    pendingImagePreviewUri  = null,
                )
            }
            is ChatIntent.ClearAttachment      -> _uiState.update {
                it.copy(
                    pendingImageB64        = null,
                    pendingImagePreviewUri = null,
                    pendingFileName        = null,
                    pendingFileContent     = null,
                )
            }
            is ChatIntent.SelectConversation   -> handleSelectConversation(intent.id)
            is ChatIntent.NewConversation      -> handleNewConversation()
            is ChatIntent.DeleteConversation   -> handleDeleteConversation(intent.id)
            is ChatIntent.RenameConversation   -> handleRenameConversation(intent.id, intent.title)
            is ChatIntent.PinConversation      -> handlePinConversation(intent.id, intent.pinned)
            is ChatIntent.ResolveConfirmation  -> handleResolveConfirmation(intent.requestId, intent.allowed)
            is ChatIntent.ClearError           -> _uiState.update { it.copy(error = null) }
            is ChatIntent.ToggleVoice          -> handleToggleVoice()
            is ChatIntent.ToggleTts            -> handleToggleTts()
            is ChatIntent.SetTtsEnabled        -> handleSetTts(intent.enabled)
            is ChatIntent.ReplayResponse       -> ttsEngine.speak(intent.text)
            is ChatIntent.RegenerateLast       -> handleRegenerate()
            is ChatIntent.CycleRoutingMode     -> handleCycleRoutingMode()
            is ChatIntent.SelectCloudModel     -> handleSelectCloudModel(intent.id)
            is ChatIntent.SelectLocalModel     -> handleSelectLocalModel(intent.id)
        }
    }

    // ── Top-bar model picker ──────────────────────────────────────────────────

    /**
     * Select a specific cloud model. Flips routing to CLOUD and stores the
     * model id so subsequent requests use it. The actual wiring from this id
     * to the outbound request shape is provider-specific — a follow-up will
     * plumb this through [ChatRepositoryImpl] so DeepSeek/Groq/etc. requests
     * are shaped for their endpoints. For Anthropic today it's already used
     * via [ApiKeyInterceptor].
     */
    private fun handleSelectCloudModel(modelId: String) {
        viewModelScope.launch {
            modelRepository.setRoutingMode(RoutingMode.CLOUD)
            // Tell the router which provider+model to use. Without persisting
            // these to ApiKeyProviderImpl the ChatRepositoryImpl direct-cloud
            // branch wouldn't know what the user picked and would fall through
            // to the default Anthropic path.
            val cloudModel = CloudModel.CATALOG.firstOrNull { it.id == modelId }
            if (cloudModel != null) {
                apiKeyProviderImpl.directProvider = cloudModel.provider
                apiKeyProviderImpl.saveDirectModel(cloudModel.provider, cloudModel.id)
            }
            // Unload whatever local model was loaded so the picker doesn't
            // show two "selected" chips at once (one on each side). Routing
            // is now CLOUD — keeping the local model in memory just wastes
            // RAM.
            val loadedId = modelRepository.observeLoadedModelId().value
            if (loadedId != null) {
                runCatching { modelRepository.unloadModel(loadedId) }
            }
            _uiState.update { it.copy(selectedCloudModelId = modelId) }
        }
    }

    /**
     * Pick a local model from the home-bar dropdown.
     *
     * If the model isn't the currently-loaded one, we kick off a load first.
     * Routing flips to LOCAL immediately so the user gets the right model
     * name in the top bar without waiting for the load to finish. The
     * streaming [loadProgress] surface lives on the Local AI screen — here
     * we just show a lightweight [loadingLocalModelId] so the picker can
     * spin on the selected row.
     */
    private fun handleSelectLocalModel(modelId: String) {
        viewModelScope.launch {
            modelRepository.setRoutingMode(RoutingMode.LOCAL)
            // Mirror handleSelectCloudModel: clear the cloud-side selection so
            // the UI shows a single "selected" chip instead of one on each
            // side. Routing is now LOCAL — the remembered cloud model would
            // otherwise still show highlighted in the cloud picker.
            _uiState.update { it.copy(selectedCloudModelId = null) }
            val currentlyLoaded = modelRepository.observeLoadedModelId().value
            if (currentlyLoaded == modelId) return@launch
            _uiState.update { it.copy(loadingLocalModelId = modelId) }
            try {
                modelRepository.loadModel(modelId).collect { /* ignore status strings */ }
            } catch (e: Exception) {
                Log.e(TAG, "loadModel from top-bar failed", e)
                _uiState.update { it.copy(error = "Load failed: ${e.message}") }
            } finally {
                _uiState.update { it.copy(loadingLocalModelId = null) }
            }
        }
    }

    // ── Send message ──────────────────────────────────────────────────────────

    private fun handleSendMessage(imageBase64: String?) {
        val state = _uiState.value
        val rawText = state.inputText.trim()

        // Pull any staged image / file if the caller didn't pass one
        // explicitly. The normal flow is now: picker stages via
        // [StageImage] / [StageFile], user types a prompt, hits Send —
        // this reads from state. The [imageBase64] param stays as a legacy
        // escape hatch.
        val effectiveB64 = imageBase64 ?: state.pendingImageB64
        val previewUri   = state.pendingImagePreviewUri
        val fileName     = state.pendingFileName
        val fileContent  = state.pendingFileContent

        val hasImage = !effectiveB64.isNullOrBlank()
        val hasFile  = !fileName.isNullOrBlank()
        // Allow image-only and file-only sends — if the user didn't type
        // anything, pick a safe default prompt based on what they attached.
        if (rawText.isBlank() && !hasImage && !hasFile) return
        if (state.isStreaming) return

        val displayText = when {
            rawText.isNotBlank() -> rawText
            hasImage             -> "Describe this image."
            hasFile              -> "Read this document and tell me what it says."
            else                 -> return
        }
        // What actually goes to the model. For attached text files we
        // prepend a clearly-delimited block so the model treats the
        // document as context instead of the user's literal prompt.
        val apiText = if (hasFile && !fileContent.isNullOrBlank()) {
            """[Attached document: $fileName]

$fileContent

---
$displayText"""
        } else displayText

        Log.i(TAG, "send '${displayText.take(60)}…' tts=${state.ttsEnabled} image=$hasImage file=$hasFile")
        ttsSpokenIndex = 0
        // Stash preview + filename so the incoming message-list flow can
        // attach them to the freshly-inserted user row once its id arrives.
        pendingPreviewForNextUserTurn  = previewUri
        pendingFileNameForNextUserTurn = fileName
        _uiState.update {
            it.copy(
                inputText              = "",
                isStreaming            = true,
                streamingText          = "",
                pendingImageB64        = null,
                pendingImagePreviewUri = null,
                pendingFileName        = null,
                pendingFileContent     = null,
            )
        }

        streamJob = viewModelScope.launch {
            val convId = ensureActiveConversation()
            // Push the enriched apiText to the repo — the user's bubble
            // shows [displayText] only, but the LLM sees the document
            // context prepended for this single turn.
            sendMessage(convId, apiText, effectiveB64, displayText)
                .collect { event -> handleChatEvent(event) }
        }
    }

    /**
     * Holds the local preview URI between [handleSendMessage] and the
     * messages-flow update that surfaces the newly-persisted user row.
     */
    private var pendingPreviewForNextUserTurn: String? = null

    /** Same idea, for text/file chips (bubble renders a filename chip). */
    private var pendingFileNameForNextUserTurn: String? = null

    /**
     * Index up to which streaming text has been pushed to TTS. Lets us emit
     * one TTS chunk per sentence boundary so audio tracks the visible text
     * in near real time, rather than starting after the whole response lands.
     */
    private var ttsSpokenIndex: Int = 0

    /** Match anywhere a clause closes. Conservative — only true sentence ends. */
    private val sentenceBoundary = Regex("[.!?…]['\"”’)\\]]?\\s+")

    private fun handleChatEvent(event: ChatEvent) {
        when (event) {
            is ChatEvent.TextDelta -> {
                _uiState.update { s -> s.copy(streamingText = s.streamingText + event.text) }
                // Stream TTS sentence-by-sentence while text grows. We only
                // emit completed sentences (boundary char + trailing space) so
                // we don't spit half-words at the user.
                if (_uiState.value.ttsEnabled) {
                    val full = _uiState.value.streamingText
                    val matches = sentenceBoundary.findAll(full).toList()
                    val lastEnd = matches.lastOrNull { it.range.last + 1 > ttsSpokenIndex }
                        ?.range?.last?.plus(1) ?: -1
                    if (lastEnd > ttsSpokenIndex) {
                        val chunk = full.substring(ttsSpokenIndex, lastEnd).trim()
                        if (chunk.isNotBlank()) ttsEngine.enqueue(chunk)
                        ttsSpokenIndex = lastEnd
                    }
                }
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
                // Real message is now in the Room flow — clear the ghost bubble.
                val finalText = _uiState.value.streamingText
                _uiState.update { it.copy(streamingText = "", activeToolCalls = emptyList()) }
                // Speak any trailing text the sentence-streamer didn't catch
                // (everything after the last sentence boundary). For non-voice
                // turns where ttsSpokenIndex is still 0, this speaks the full
                // text — same behaviour as the old code path.
                if (_uiState.value.ttsEnabled) {
                    val tail = finalText.substring(ttsSpokenIndex.coerceAtMost(finalText.length))
                    if (tail.isNotBlank()) ttsEngine.enqueue(tail)
                }
                ttsSpokenIndex = 0
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

        // Don't pre-emptively stop TTS here — we want duplex audio: JARVIS can
        // be mid-sentence and the mic can still be hot. The Android recognizer
        // uses VOICE_RECOGNITION audio source, which enables acoustic echo
        // cancellation on Samsung / Pixel hardware, so speaker output rarely
        // triggers a false onBeginningOfSpeech. If the user actually speaks
        // while TTS is playing, onBeginningOfSpeech below fires the barge-in.

        val recognizer = speechRecognizer ?: SpeechRecognizer.createSpeechRecognizer(context).also {
            speechRecognizer = it
        }

        recognizer.setRecognitionListener(object : RecognitionListener {
            override fun onReadyForSpeech(params: Bundle?) {
                _uiState.update { it.copy(isRecording = true) }
            }
            override fun onBeginningOfSpeech() {
                // Intentionally NOT a barge-in trigger. On this device the mic
                // (even with the VOICE_RECOGNITION source) picks up JARVIS's
                // own TTS playing through the media channel and fires this
                // immediately after TTS starts — that used to kill the audio
                // ~1s in with no actual user speech. See onPartialResults
                // below for the real barge-in, which needs transcribed words.
            }
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() {
                // Don't flip isRecording here — onResults below is the atomic
                // moment where the final transcript becomes inputText AND the
                // recording flag flips off together. If we set isRecording
                // false here, a watcher could fire SendMessage with the latest
                // partial, clear inputText, then onResults restores the final
                // text — producing a stuck "ghost" message in the input field.
            }
            override fun onError(error: Int) {
                // ERROR_NO_MATCH (7) and ERROR_SPEECH_TIMEOUT (6) are normal
                // "user paused too long" outcomes — drop the partial that was
                // sitting in the input field so the auto-restart loop in
                // ChatScreen will re-arm the mic immediately. For other
                // errors (network, server, busy) just clear the recording
                // flag and let the loop try again on the next idle tick.
                val recoverable = error == 7 /* NO_MATCH */ ||
                                  error == 6 /* SPEECH_TIMEOUT */
                _uiState.update {
                    it.copy(
                        isRecording = false,
                        inputText   = if (recoverable) "" else it.inputText,
                    )
                }
                Log.w(TAG, "STT error: $error (recoverable=$recoverable)")
            }
            override fun onResults(results: Bundle?) {
                val matches = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                val text = matches?.firstOrNull()
                if (text == null) {
                    // Recognizer finished with no usable text — release the
                    // recording flag so the auto-restart loop can re-arm
                    // without a leftover transcript blocking the next turn.
                    _uiState.update { it.copy(isRecording = false) }
                    return
                }
                _uiState.update { it.copy(inputText = text, isRecording = false) }
            }
            override fun onPartialResults(partial: Bundle?) {
                val matches = partial?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                val text = matches?.firstOrNull() ?: return
                _uiState.update { it.copy(inputText = text) }
                // Real barge-in: the recognizer produced an actual partial
                // transcript, which means words were matched (not just noise
                // / echo from our own TTS). Stop TTS so the user is heard.
                // Guarded on length to ignore single-char false positives.
                if (text.length >= 2 && ttsEngine.isSpeaking.value) {
                    Log.i(TAG, "Barge-in: partial='${text.take(20)}' — stopping TTS")
                    ttsEngine.stop()
                }
            }
            override fun onEvent(eventType: Int, params: Bundle?) {}
        })

        val intent = android.content.Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault())
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
            // Samsung's default silence-end threshold is ~1.5 s, so if the
            // user takes a breath between "hey" and the rest of the
            // sentence, the recognizer ends with NO_MATCH and the overlay
            // feels unresponsive. Widen the timeouts so the recognizer
            // waits long enough for natural speech.
            //
            //   - MINIMUM_LENGTH         = keep listening at least this long
            //   - POSSIBLY_COMPLETE_...  = soft cutoff after a trailing pause
            //   - COMPLETE_SILENCE_...   = hard cutoff after trailing silence
            putExtra(
                RecognizerIntent.EXTRA_SPEECH_INPUT_MINIMUM_LENGTH_MILLIS,
                2_000L,
            )
            putExtra(
                RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS,
                2_000L,
            )
            putExtra(
                RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS,
                3_500L,
            )
            // Force Google's online recognizer — on-device recognition on
            // Samsung is noticeably less sensitive and misses softly-spoken
            // prompts, which is exactly the failure mode NO_MATCH-spam was
            // pointing at. Online recognition trades latency for accuracy,
            // which is the right call in voice-mode.
            putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, false)
            // Some Samsung builds require a calling-package hint, otherwise
            // the recognizer silently downgrades.
            putExtra(
                "calling_package",
                context.packageName,
            )
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

    private fun handleSetTts(enabled: Boolean) {
        if (_uiState.value.ttsEnabled == enabled) return
        ttsEngine.setEnabled(enabled)
        _uiState.update { it.copy(ttsEnabled = enabled) }
    }

    /**
     * Delete the most-recent assistant turn and re-stream a reply to the
     * preceding user turn. The streaming API sees the conversation minus the
     * assistant reply, so it produces a new one. Works for any provider that
     * ChatRepository.sendMessage already supports.
     */
    private fun handleRegenerate() {
        val convId = _uiState.value.activeConversationId ?: return
        if (_uiState.value.isStreaming) return
        viewModelScope.launch {
            // Room returns newest-first already via conversation_id index, but
            // we sort defensively since callers may have changed the DAO.
            val recent = messageDao.getRecentByConversation(convId, limit = 20)
                .sortedByDescending { it.timestamp }
            val lastAssistant = recent.firstOrNull { it.role == "assistant" }
                ?: return@launch
            val lastUser = recent.firstOrNull {
                it.role == "user" && it.timestamp < lastAssistant.timestamp
            } ?: return@launch

            messageDao.deleteById(lastAssistant.id)
            _uiState.update { it.copy(isStreaming = true, streamingText = "") }
            ttsSpokenIndex = 0
            streamJob = viewModelScope.launch {
                sendMessage(convId, lastUser.content, null)
                    .collect { event -> handleChatEvent(event) }
            }
        }
    }

    // ── Routing mode cycle ────────────────────────────────────────────────────
    //
    // The home top-bar toggle only cycles between the two modes the user
    // actually cares about at send-time: LOCAL (stay on-device) and CLOUD
    // (hit the API). AUTO/HYBRID still exist for the agent loop and can be
    // set from the Models screen's routing-mode row, but on the home bar
    // the binary toggle removes a layer of ambiguity about where a message
    // is going.

    private fun handleCycleRoutingMode() {
        viewModelScope.launch {
            val current = modelRepository.observeRoutingMode().value
            val next = when (current) {
                RoutingMode.LOCAL -> RoutingMode.CLOUD
                else              -> RoutingMode.LOCAL
            }
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
            .onEach  { list ->
                // Bind any pending preview URI / filename (stashed by
                // [handleSendMessage]) to the latest unmapped user row so
                // the bubble can render the image inline and/or the
                // filename chip above the prompt.
                val currentImages = _uiState.value.sentImagePaths
                val currentFiles  = _uiState.value.sentFileNames

                val updatedImages: Map<Long, String> =
                    pendingPreviewForNextUserTurn?.let { previewUri ->
                        val target = list.lastOrNull {
                            it.role == com.jarvis.android.domain.model.MessageRole.USER &&
                            it.contentType == com.jarvis.android.domain.model.MessageContentType.IMAGE &&
                            currentImages[it.id] == null
                        }
                        if (target != null) {
                            pendingPreviewForNextUserTurn = null
                            currentImages + (target.id to previewUri)
                        } else currentImages
                    } ?: currentImages

                val updatedFiles: Map<Long, String> =
                    pendingFileNameForNextUserTurn?.let { name ->
                        val highestMapped = currentFiles.keys.maxOrNull() ?: Long.MIN_VALUE
                        val target = list.lastOrNull {
                            it.role == com.jarvis.android.domain.model.MessageRole.USER &&
                            it.id > highestMapped &&
                            currentFiles[it.id] == null
                        }
                        if (target != null) {
                            pendingFileNameForNextUserTurn = null
                            currentFiles + (target.id to name)
                        } else currentFiles
                    } ?: currentFiles

                _uiState.update {
                    it.copy(
                        messages       = list,
                        sentImagePaths = updatedImages,
                        sentFileNames  = updatedFiles,
                    )
                }
            }
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
