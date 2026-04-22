package com.jarvis.android.presentation.chat

import android.content.Context
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import com.jarvis.android.data.repository.ApiKeyProviderImpl
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch
import java.util.Locale
import java.util.UUID
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Thin wrapper around Android [TextToSpeech] that exposes a [isSpeaking] StateFlow
 * so the chat UI can animate the voice sphere while JARVIS is talking.
 *
 * Usage: call [speak] with the full assistant response text.
 * The engine is lazy-initialized on first use.
 */
@Singleton
class JarvisTtsEngine @Inject constructor(
    @ApplicationContext private val context: Context,
    private val apiKeyProvider: ApiKeyProviderImpl,
    private val brainTts:       BrainTtsClient,
) {

    // Mirror BrainTtsClient's flows into our own so the rest of the app can
    // observe a single isSpeaking / speechTick regardless of which backend
    // is active. Wired in init below.
    private val mirrorScope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)

    init {
        mirrorScope.launch {
            brainTts.isSpeaking.collectLatest { speaking ->
                if (speaking) _isSpeaking.value = true
                // Don't drop _isSpeaking on Brain stop — local TTS may still
                // be active. The local-TTS onDone handler clears it.
            }
        }
        mirrorScope.launch {
            brainTts.speechTick.collectLatest { t ->
                if (t > 0L) _speechTick.value = t
            }
        }
    }

    /** True when remote brain-server TTS is active for this session. */
    private fun useRemoteTts(): Boolean = apiKeyProvider.getBrainTtsUrl().isNotBlank()

    private var tts: TextToSpeech? = null
    private var isReady = false

    private val _isSpeaking = MutableStateFlow(false)
    val isSpeaking: StateFlow<Boolean> = _isSpeaking.asStateFlow()

    /**
     * Monotonically-increasing tick that bumps every time the TTS engine
     * starts speaking a NEW range (typically a word). Subscribers can use this
     * as a "speech heartbeat" to drive per-word visualisations — the closest
     * equivalent to real audio-amplitude data without needing the Visualizer
     * API and its restricted permissions on Android 11+.
     */
    private val _speechTick = MutableStateFlow(0L)
    val speechTick: StateFlow<Long> = _speechTick.asStateFlow()

    // Off by default — see ChatUiState.ttsEnabled. Voice mode flips this on
    // explicitly when the user opens the voice overlay.
    private var enabled = false

    fun setEnabled(value: Boolean) {
        enabled = value
        if (!value) stop()
    }

    fun isEnabled(): Boolean = enabled

    /** Speak [text] if TTS is enabled. Interrupts any in-progress speech. */
    fun speak(text: String) {
        if (!enabled) {
            Log.d(TAG, "speak() ignored — TTS disabled (text len=${text.length})")
            return
        }
        if (text.isBlank()) return
        Log.i(TAG, "speak() text-len=${text.length} preview='${text.take(40)}…'")
        if (useRemoteTts()) {
            brainTts.speak(apiKeyProvider.getBrainTtsUrl(), text)
        }
        // Always also speak via local TTS as a safety fallback. If brain TTS
        // succeeds, both will play — that's audible noise. To avoid that,
        // only call local when remote isn't configured. The remote backend
        // logs failures so the user can see the issue in logcat.
        if (!useRemoteTts()) {
            _isSpeaking.value = true
            ensureReady {
                val utteranceId = UUID.randomUUID().toString()
                val result = tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, utteranceId)
                Log.i(TAG, "tts.speak() returned $result for utterance $utteranceId")
            }
        }
    }

    /**
     * Append [text] to the speaking queue without flushing what's already
     * playing. Used by sentence-streaming so each sentence chains naturally
     * with the previous one — voice tracks the streaming text in near real
     * time instead of starting after the full response finishes.
     */
    fun enqueue(text: String) {
        if (!enabled || text.isBlank()) return
        Log.i(TAG, "enqueue() text-len=${text.length} preview='${text.take(40)}…'")
        if (useRemoteTts()) {
            brainTts.enqueue(apiKeyProvider.getBrainTtsUrl(), text)
            return
        }
        _isSpeaking.value = true
        ensureReady {
            val utteranceId = UUID.randomUUID().toString()
            tts?.speak(text, TextToSpeech.QUEUE_ADD, null, utteranceId)
        }
    }

    /** Stop speaking immediately — both backends. */
    fun stop() {
        tts?.stop()
        brainTts.stop()
        _isSpeaking.value = false
    }

    /** Release TTS engine resources. Call from onCleared() or when no longer needed. */
    fun shutdown() {
        tts?.shutdown()
        tts = null
        isReady = false
        _isSpeaking.value = false
    }

    private fun ensureReady(block: () -> Unit) {
        if (isReady) {
            block()
            return
        }
        tts = TextToSpeech(context) { status ->
            if (status == TextToSpeech.SUCCESS) {
                tts?.language = Locale.US
                tts?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                    override fun onStart(utteranceId: String) {
                        _isSpeaking.value = true
                        // Initial tick so consumers see the first word boundary
                        // even before onRangeStart fires.
                        _speechTick.value = System.currentTimeMillis()
                    }
                    override fun onDone(utteranceId: String) {
                        _isSpeaking.value = false
                    }
                    @Deprecated("Deprecated in API 21")
                    override fun onError(utteranceId: String) {
                        _isSpeaking.value = false
                    }
                    /**
                     * Fires when the TTS engine begins speaking a specific
                     * substring of the current utterance — usually per word.
                     * Each call bumps [speechTick]; the voice overlay watches
                     * this and pulses its glow on every tick, so the animation
                     * follows the actual audio output cadence.
                     */
                    override fun onRangeStart(
                        utteranceId: String,
                        start:       Int,
                        end:         Int,
                        frame:       Int,
                    ) {
                        _speechTick.value = System.currentTimeMillis()
                    }
                })
                isReady = true
                block()
                Log.i(TAG, "TTS engine ready")
            } else {
                Log.e(TAG, "TTS init failed: status=$status")
            }
        }
    }

    companion object {
        private const val TAG = "JarvisTtsEngine"
    }
}
