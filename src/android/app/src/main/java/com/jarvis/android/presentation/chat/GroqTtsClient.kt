package com.jarvis.android.presentation.chat

import android.content.Context
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.util.Log
import com.jarvis.android.data.repository.ApiKeyProviderImpl
import com.jarvis.android.domain.model.CloudProvider
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton
import kotlin.coroutines.resume

/**
 * Text-to-speech via Groq's PlayAI endpoint
 * (`https://api.groq.com/openai/v1/audio/speech`, model `playai-tts`).
 *
 * This is what the user's Groq Playground uses — same endpoint, same
 * voices, no anti-abuse tokens to rotate. Reuses the Groq API key already
 * stored for chat, so zero additional setup for users who already have
 * Groq configured.
 *
 * Public API mirrors [BrainTtsClient] exactly so [JarvisTtsEngine] can
 * swap between backends without the rest of the app noticing:
 *   - [enqueue] / [speak] / [stop]
 *   - [isSpeaking] / [speechTick] StateFlows for the voice-overlay glow
 *
 * ## Sentence streaming
 *
 * `ChatRepositoryImpl.sendLocal()` splits the model's reply into sentences
 * and calls [enqueue] per sentence. Each call adds to our FIFO queue; a
 * single drain coroutine POSTs them to Groq one at a time and feeds the
 * WAVs into MediaPlayer sequentially. This keeps TTS roughly in sync with
 * the streamed text — the first sentence starts playing ~500 ms after the
 * model produces it, rather than waiting for the whole reply.
 */
@Singleton
class GroqTtsClient @Inject constructor(
    @ApplicationContext private val context: Context,
    private val apiKeyProvider: ApiKeyProviderImpl,
) {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val httpClient: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .retryOnConnectionFailure(true)
            .build()
    }

    private val queue = ConcurrentLinkedQueue<String>()
    @Volatile private var current: MediaPlayer? = null
    @Volatile private var playerJob: Job? = null
    @Volatile private var tickJob: Job? = null

    private val _isSpeaking = MutableStateFlow(false)
    val isSpeaking: StateFlow<Boolean> = _isSpeaking.asStateFlow()

    private val _speechTick = MutableStateFlow(0L)
    val speechTick: StateFlow<Long> = _speechTick.asStateFlow()

    /**
     * Emits the original text of any utterance we failed to synthesize — HTTP
     * error, empty body, network timeout, expired key, anything that makes
     * [synthesize] return null. [JarvisTtsEngine] subscribes and re-routes
     * the text to local Android TTS so the user always hears *something*
     * instead of silence. BufferOverflow.DROP_OLDEST means a burst of failed
     * sentences (e.g. every chunk of a streamed reply) can't block the drain
     * loop if the engine is slow to drain them.
     */
    private val _failures = MutableSharedFlow<String>(
        replay        = 0,
        extraBufferCapacity = 32,
        onBufferOverflow    = BufferOverflow.DROP_OLDEST,
    )
    val failures: SharedFlow<String> = _failures.asSharedFlow()

    // ── Public API ────────────────────────────────────────────────────────────

    fun enqueue(text: String) {
        if (text.isBlank()) return
        if (!hasCredentials()) {
            Log.w(TAG, "enqueue() ignored — no Groq API key configured")
            return
        }
        queue.offer(text)
        _isSpeaking.value = true
        if (playerJob == null) {
            playerJob = scope.launch { drain() }
        }
    }

    fun speak(text: String) {
        if (text.isBlank()) return
        queue.clear()
        stopCurrent()
        enqueue(text)
    }

    fun stop() {
        queue.clear()
        playerJob?.cancel(); playerJob = null
        stopCurrent()
        _isSpeaking.value = false
    }

    /** True when we have the API key needed to call Groq. */
    fun hasCredentials(): Boolean =
        apiKeyProvider.getProviderKey(CloudProvider.GROQ).isNotBlank()

    private fun stopCurrent() {
        tickJob?.cancel(); tickJob = null
        runCatching { current?.stop() }
        runCatching { current?.release() }
        current = null
    }

    // ── Queue drain ───────────────────────────────────────────────────────────

    private suspend fun drain() {
        try {
            while (true) {
                val text = queue.poll() ?: break
                val wav  = synthesize(text)
                if (wav == null) {
                    // Cloud call failed — tell JarvisTtsEngine so the text
                    // gets re-routed to local Android TTS. Without this the
                    // sentence would be silently dropped.
                    Log.w(TAG, "synthesize failed for '${text.take(40)}…' → emitting failure for local TTS fallback")
                    _failures.tryEmit(text)
                    continue
                }
                playOne(wav)
            }
        } finally {
            playerJob = null
            if (queue.isEmpty()) _isSpeaking.value = false
        }
    }

    /**
     * POST to Groq's `/v1/audio/speech` and save the WAV body to a temp
     * file. Returns null on any failure (no key, HTTP error, empty body);
     * the drain loop skips to the next sentence on null. JarvisTtsEngine
     * will fall back to local Android TTS if Groq is disabled or its
     * credentials disappear between sentences.
     */
    /**
     * Synthesize + play a single sample with a specific voice — used by the
     * Voice Settings "Preview" button. Bypasses the queue / streaming
     * machinery so the sample never interleaves with a live chat reply.
     * Returns `true` on success, `false` if the backend refused (400/401/
     * network) — the caller surfaces the result as a snackbar so the user
     * knows their chosen voice is actually reachable.
     */
    suspend fun preview(text: String, voiceOverride: String): Boolean {
        stop()
        val wav = synthesize(text, voiceOverride) ?: return false
        playOne(wav)
        return true
    }

    private suspend fun synthesize(
        text: String,
        voiceOverride: String? = null,
    ): File? = withContext(Dispatchers.IO) {
        val apiKey = apiKeyProvider.getProviderKey(CloudProvider.GROQ)
        if (apiKey.isBlank()) {
            Log.w(TAG, "synthesize: empty Groq API key")
            return@withContext null
        }
        // Resolve the voice through the legacy migration so pref values
        // saved under the PlayAI names ("Fritz-PlayAI" etc) get upgraded
        // to the closest Orpheus equivalent. Without this, users who set
        // a voice on a pre-Orpheus build get a 400 on every utterance
        // because Orpheus rejects the old IDs.
        val voice = com.jarvis.android.presentation.settings.GroqTtsVoice
            .migrateLegacyVoiceId(voiceOverride ?: apiKeyProvider.getGroqTtsVoice())
        val escapedInput = kotlinx.serialization.json.Json.encodeToString(
            JsonPrimitive.serializer(),
            JsonPrimitive(text),
        )
        // Model: canopylabs/orpheus-v1-english. Groq decommissioned the
        // previous `playai-tts` model in April 2026 (HTTP 400,
        // `code: model_decommissioned`), migrated TTS to Canopy Labs'
        // Orpheus v1. Same OpenAI-compatible /v1/audio/speech shape.
        //
        // Response format: WAV. Orpheus only accepts `wav` — asking for
        // `mp3` returns HTTP 400 `response_format must be one of [wav]`.
        // The WAV Groq returns is a streaming RIFF with `size=0xFFFFFFFF`
        // plus a `LIST/INFO` chunk between `fmt ` and `data`; Android's
        // MediaPlayer rejects both on Samsung Android 16, so we sanitize
        // the header after download ([sanitizeOrpheusWav]).
        val body = """
            {"model":"canopylabs/orpheus-v1-english","voice":"$voice","input":$escapedInput,"response_format":"wav"}
        """.trimIndent().toRequestBody(JSON)
        val req = Request.Builder()
            .url(ENDPOINT)
            .post(body)
            .header("Authorization", "Bearer $apiKey")
            .header("Content-Type",  "application/json")
            .header("Accept",        "audio/wav")
            .build()

        runCatching {
            httpClient.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) {
                    val code = resp.code
                    val errBody = runCatching {
                        resp.body?.string()?.take(300)
                    }.getOrNull().orEmpty()
                    Log.w(TAG, "Groq TTS HTTP $code: $errBody")
                    return@use null
                }
                val respBody = resp.body
                if (respBody == null) {
                    Log.w(TAG, "Groq TTS: empty body")
                    return@use null
                }
                val tmp = File.createTempFile("groq-tts-", ".wav", context.cacheDir)
                tmp.outputStream().use { out -> respBody.byteStream().copyTo(out) }
                if (tmp.length() < 128) {
                    // Sanity floor: a real WAV is at least a header + a few
                    // frames; an empty/error-JSON body slipping through a
                    // 200 OK would be much smaller.
                    Log.w(TAG, "Groq TTS: suspiciously small WAV (${tmp.length()} bytes)")
                    tmp.delete()
                    return@use null
                }
                if (!sanitizeOrpheusWav(tmp)) {
                    // Leave the file as-is; MediaPlayer will try and may
                    // fail with prepare() — the voice-comm/media fallback
                    // in playOne handles that. Log so future debug sessions
                    // can see the sanitizer bailed.
                    Log.w(TAG, "Groq TTS: WAV sanitizer could not rewrite header — handing raw bytes to MediaPlayer")
                }
                Log.d(TAG, "Groq TTS synthesized ${tmp.length() / 1024} KB for '${text.take(40)}…'")
                tmp
            }
        }.getOrElse {
            Log.w(TAG, "Groq TTS request failed: ${it.message}")
            null
        }
    }

    // ── Playback ──────────────────────────────────────────────────────────────

    private suspend fun playOne(wav: File) = withContext(Dispatchers.Main) {
        // MediaPlayer.prepare() fails with status=0x1 on Samsung/Android 16
        // when USAGE_VOICE_COMMUNICATION meets a 24 kHz PCM WAV (Orpheus's
        // native rate) — the voice-comm audio pipeline wants 16 kHz. We
        // try voice-comm first for Samsung's hardware AEC, then fall back
        // to USAGE_MEDIA so playback succeeds even at the cost of losing
        // echo cancellation. (Empty catch blocks also cover the case of a
        // torn-down MediaPlayer during stopCurrent mid-prepare.)
        fun buildPlayer(usage: Int): MediaPlayer = MediaPlayer().apply {
            setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(usage)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            setDataSource(wav.absolutePath)
            prepare()
        }
        val mp: MediaPlayer = runCatching {
            buildPlayer(AudioAttributes.USAGE_VOICE_COMMUNICATION)
        }.getOrElse { e1 ->
            Log.w(TAG, "MediaPlayer voice-comm prepare failed (${e1.message}); retrying with MEDIA")
            runCatching { buildPlayer(AudioAttributes.USAGE_MEDIA) }.getOrElse { e2 ->
                Log.w(TAG, "MediaPlayer MEDIA prepare also failed: ${e2.message} — routing to local TTS")
                runCatching { wav.delete() }
                return@withContext
            }
        }
        current = mp

        // Per-word tick approximation — voice-overlay glow watches this.
        tickJob = scope.launch {
            while (mp.isPlaying || _isSpeaking.value) {
                _speechTick.value = System.currentTimeMillis()
                delay(140)
            }
        }

        mp.start()
        suspendCancellableCoroutine { cont ->
            mp.setOnCompletionListener {
                runCatching { mp.release() }
                current = null
                tickJob?.cancel()
                wav.delete()
                if (queue.isEmpty()) _isSpeaking.value = false
                cont.resume(Unit)
            }
            cont.invokeOnCancellation {
                runCatching { mp.stop() }
                runCatching { mp.release() }
                wav.delete()
            }
        }
    }

    /**
     * Rewrite Orpheus's streaming WAV into a minimal, well-formed RIFF that
     * MediaPlayer's prepare() accepts.
     *
     * Orpheus returns: `RIFF <0xFFFFFFFF> WAVE fmt  <fmtSize><fmt> LIST
     * <listSize><list bytes> data <maybe 0xFFFFFFFF><pcm>`. The unknown
     * RIFF size, the unknown data size, and the intervening LIST/INFO
     * chunk each independently break Samsung Android 16's MediaPlayer,
     * producing the silent-fallback "voice doesn't change" bug.
     *
     * We walk the chunks to find `fmt ` and `data`, then overwrite the
     * file with `RIFF/WAVE/fmt /data` only — correct sizes, no metadata.
     * Idempotent for already-clean WAVs.
     *
     * Returns true on successful rewrite, false if the bytes don't look
     * like a WAV we recognise (caller logs and hands raw bytes to
     * MediaPlayer as a last resort).
     */
    private fun sanitizeOrpheusWav(file: File): Boolean {
        val bytes = runCatching { file.readBytes() }.getOrNull() ?: return false
        if (bytes.size < 44) return false
        if (String(bytes, 0, 4) != "RIFF" || String(bytes, 8, 4) != "WAVE") return false
        if (String(bytes, 12, 4) != "fmt ") return false

        val bb = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
        val fmtSize = bb.getInt(16)
        if (fmtSize <= 0 || 20 + fmtSize > bytes.size) return false
        val fmtData = bytes.copyOfRange(20, 20 + fmtSize)

        // Walk chunks after `fmt ` until we hit `data`; skip anything else
        // (LIST/INFO in Orpheus's case, but also tolerates JUNK/bext/etc).
        var pos = 20 + fmtSize
        while (pos + 8 <= bytes.size) {
            val tag = String(bytes, pos, 4)
            val chunkSize = bb.getInt(pos + 4)
            if (tag == "data") {
                val dataStart = pos + 8
                // Streaming WAV may set size to 0xFFFFFFFF (read as -1) or a
                // value that exceeds the downloaded bytes — in both cases,
                // compute from the remaining file length.
                val dataLen = if (chunkSize == -1 || dataStart + chunkSize > bytes.size) {
                    bytes.size - dataStart
                } else {
                    chunkSize
                }
                if (dataLen <= 0) return false
                val pcm = bytes.copyOfRange(dataStart, dataStart + dataLen)

                val out = ByteBuffer.allocate(12 + 8 + fmtSize + 8 + dataLen)
                    .order(ByteOrder.LITTLE_ENDIAN)
                out.put("RIFF".toByteArray(Charsets.US_ASCII))
                out.putInt(4 + (8 + fmtSize) + (8 + dataLen))
                out.put("WAVE".toByteArray(Charsets.US_ASCII))
                out.put("fmt ".toByteArray(Charsets.US_ASCII))
                out.putInt(fmtSize)
                out.put(fmtData)
                out.put("data".toByteArray(Charsets.US_ASCII))
                out.putInt(dataLen)
                out.put(pcm)
                file.writeBytes(out.array())
                return true
            }
            // Non-data chunk: skip past it. RIFF chunk sizes are word-aligned,
            // so odd sizes carry a trailing pad byte. Bail on a garbage size
            // rather than walking off the end of the buffer.
            if (chunkSize < 0 || pos + 8 + chunkSize > bytes.size) return false
            pos += 8 + chunkSize + (chunkSize and 1)
        }
        return false
    }

    companion object {
        private const val TAG      = "GroqTtsClient"
        private const val ENDPOINT = "https://api.groq.com/openai/v1/audio/speech"
        private val JSON           = "application/json; charset=utf-8".toMediaType()
    }
}
