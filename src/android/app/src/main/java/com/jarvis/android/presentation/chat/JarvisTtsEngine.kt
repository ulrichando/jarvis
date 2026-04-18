package com.jarvis.android.presentation.chat

import android.content.Context
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
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
) {

    private var tts: TextToSpeech? = null
    private var isReady = false

    private val _isSpeaking = MutableStateFlow(false)
    val isSpeaking: StateFlow<Boolean> = _isSpeaking.asStateFlow()

    private var enabled = true

    fun setEnabled(value: Boolean) {
        enabled = value
        if (!value) stop()
    }

    fun isEnabled(): Boolean = enabled

    /** Speak [text] if TTS is enabled. Interrupts any in-progress speech. */
    fun speak(text: String) {
        if (!enabled || text.isBlank()) return
        ensureReady {
            val utteranceId = UUID.randomUUID().toString()
            tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, utteranceId)
        }
    }

    /** Stop speaking immediately. */
    fun stop() {
        tts?.stop()
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
                    }
                    override fun onDone(utteranceId: String) {
                        _isSpeaking.value = false
                    }
                    @Deprecated("Deprecated in API 21")
                    override fun onError(utteranceId: String) {
                        _isSpeaking.value = false
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
