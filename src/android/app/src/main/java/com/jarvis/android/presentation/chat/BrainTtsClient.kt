package com.jarvis.android.presentation.chat

import android.content.Context
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.util.Log
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.File
import java.util.UUID
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Streams text-to-speech audio from the user's homelab brain server
 * (`POST /tts` → audio/wav) and plays it through Android's MediaPlayer.
 * This gives the phone the same voice the user hears on their computer
 * (Groq-backed, configured server-side) instead of Android's local TTS.
 *
 * Sentences are queued: while the model streams its response, each completed
 * sentence is enqueued and played back in order, so audio tracks the text
 * in near real time. The progress tick approximates per-word callbacks by
 * polling [MediaPlayer.getCurrentPosition] every ~140ms — visualisation
 * (the voice-overlay glow) reads it via [speechTick].
 */
@Singleton
class BrainTtsClient @Inject constructor(
    @ApplicationContext private val context: Context,
) {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val httpClient: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(120, TimeUnit.SECONDS)
            .build()
    }

    private val queue = ConcurrentLinkedQueue<String>()
    @Volatile private var current: MediaPlayer? = null
    @Volatile private var playerJob: Job? = null
    @Volatile private var tickJob: Job? = null

    private val _isSpeaking = MutableStateFlow(false)
    val isSpeaking: StateFlow<Boolean> = _isSpeaking.asStateFlow()

    /** Bumps roughly per-word as MediaPlayer position advances. */
    private val _speechTick = MutableStateFlow(0L)
    val speechTick: StateFlow<Long> = _speechTick.asStateFlow()

    /** Append [text] to the speaking queue. Starts playback if idle. */
    fun enqueue(baseUrl: String, text: String) {
        if (text.isBlank() || baseUrl.isBlank()) return
        queue.offer(text)
        _isSpeaking.value = true
        if (playerJob == null) {
            playerJob = scope.launch { drain(baseUrl) }
        }
    }

    /** Replace the queue with a single utterance. */
    fun speak(baseUrl: String, text: String) {
        if (text.isBlank() || baseUrl.isBlank()) return
        queue.clear()
        stopCurrent()
        enqueue(baseUrl, text)
    }

    /** Cancel everything — flush queue, kill the active player, drop ticks. */
    fun stop() {
        queue.clear()
        playerJob?.cancel(); playerJob = null
        stopCurrent()
        _isSpeaking.value = false
    }

    private fun stopCurrent() {
        tickJob?.cancel(); tickJob = null
        runCatching { current?.stop() }
        runCatching { current?.release() }
        current = null
    }

    private suspend fun drain(baseUrl: String) {
        try {
            while (true) {
                val text = queue.poll() ?: break
                val wav  = fetchWav(baseUrl, text) ?: continue
                playOne(wav)
            }
        } finally {
            playerJob = null
            if (queue.isEmpty()) _isSpeaking.value = false
        }
    }

    /** POST `{ "text": ... }` to `<baseUrl>/tts`, write the WAV to a temp file. */
    private suspend fun fetchWav(baseUrl: String, text: String): File? = withContext(Dispatchers.IO) {
        val url = baseUrl.trimEnd('/') + "/tts"
        val body = """{"text":${kotlinx.serialization.json.JsonPrimitive(text)}}""".toRequestBody(JSON)
        val req  = Request.Builder()
            .url(url)
            .post(body)
            .header("Accept", "audio/wav")
            .build()
        runCatching {
            httpClient.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful || resp.body == null) {
                    Log.w(TAG, "TTS upstream ${resp.code}")
                    return@use null
                }
                val tmp = File.createTempFile("brain-tts-", ".wav", context.cacheDir)
                tmp.outputStream().use { out -> resp.body!!.byteStream().copyTo(out) }
                tmp
            }
        }.getOrElse {
            Log.w(TAG, "TTS fetch failed: ${it.message}")
            null
        }
    }

    private suspend fun playOne(wav: File) = withContext(Dispatchers.Main) {
        // See GroqTtsClient.playOne for why prepare() needs try/catch —
        // a corrupt WAV body or a torn-down MediaPlayer mid-prepare was
        // crashing the whole app.
        val mp = try {
            MediaPlayer().apply {
                setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build(),
                )
                setDataSource(wav.absolutePath)
                prepare()
            }
        } catch (e: Exception) {
            Log.w(TAG, "MediaPlayer.prepare failed: ${e.message}")
            runCatching { wav.delete() }
            return@withContext
        }
        current = mp

        // Approximate per-word ticks by sampling currentPosition every ~140ms.
        tickJob = scope.launch {
            while (mp.isPlaying || _isSpeaking.value) {
                _speechTick.value = System.currentTimeMillis()
                delay(140)
                if (current !== mp) break
            }
        }

        val done = kotlinx.coroutines.CompletableDeferred<Unit>()
        mp.setOnCompletionListener { done.complete(Unit) }
        mp.setOnErrorListener { _, what, extra ->
            Log.w(TAG, "MediaPlayer error what=$what extra=$extra")
            done.complete(Unit); true
        }
        mp.start()
        done.await()

        tickJob?.cancel(); tickJob = null
        runCatching { mp.release() }
        if (current === mp) current = null
        runCatching { wav.delete() }
    }

    fun shutdown() {
        stop()
        scope.cancel()
    }

    companion object {
        private const val TAG = "BrainTtsClient"
        private val JSON = "application/json; charset=utf-8".toMediaType()
    }
}
