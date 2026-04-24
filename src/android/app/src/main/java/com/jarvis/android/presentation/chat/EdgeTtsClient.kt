package com.jarvis.android.presentation.chat

import android.content.Context
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.util.Log
import com.jarvis.android.data.repository.ApiKeyProviderImpl
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
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import java.io.ByteArrayOutputStream
import java.io.File
import java.security.MessageDigest
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.UUID
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton
import kotlin.coroutines.resume

/**
 * Text-to-speech via Microsoft Edge's free WebSocket endpoint — the same
 * one the Edge browser's "Read Aloud" feature uses. No API key, no
 * homelab server, just a WebSocket to
 * `speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1`
 * with a fixed `TrustedClientToken` that Microsoft ships in Edge itself.
 *
 * Emits the same `isSpeaking` + `speechTick` StateFlow surface as
 * [BrainTtsClient] so the chat overlay animation code doesn't know (or
 * care) which backend is actually speaking.
 *
 * ## Voice selection
 *
 * Default is `en-GB-RyanNeural` — closest to the Iron Man JARVIS tone
 * among the free Edge voices. Change via [voice] — any of:
 *   en-GB-RyanNeural / en-GB-ThomasNeural      ← British male (JARVIS-y)
 *   en-US-GuyNeural / en-US-EricNeural
 *   en-US-ChristopherNeural / en-US-DavisNeural ← US male, deeper
 *   en-US-BrianMultilingualNeural               ← handles multiple languages
 *
 * ## Protocol notes
 *
 * The Edge TTS WebSocket returns binary frames where the first 2 bytes
 * are a big-endian uint16 header-length, followed by an ASCII header
 * (like `Path:audio\r\n...`), and the remaining bytes are MP3 audio.
 * Text frames (Path:turn.start, Path:audio.metadata, Path:turn.end)
 * mark the lifecycle. We accumulate MP3 bytes from the `Path:audio`
 * binary frames, then hand the whole file to [MediaPlayer] once
 * `turn.end` arrives.
 */
@Singleton
class EdgeTtsClient @Inject constructor(
    @ApplicationContext private val context: Context,
    private val apiKeyProvider: ApiKeyProviderImpl,
) {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val httpClient: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(120, TimeUnit.SECONDS)
            .pingInterval(20, TimeUnit.SECONDS)
            .build()
    }

    private val queue = ConcurrentLinkedQueue<String>()
    @Volatile private var current: MediaPlayer? = null
    @Volatile private var playerJob: Job? = null
    @Volatile private var tickJob: Job? = null

    /**
     * Resolves the active Edge TTS voice from the saved preference. Re-read
     * every synthesize() call so the user picking a new voice in Settings
     * takes effect on the next sentence JARVIS speaks — no restart needed.
     */
    private val voice: String
        get() = apiKeyProvider.getEdgeTtsVoice()

    private val _isSpeaking = MutableStateFlow(false)
    val isSpeaking: StateFlow<Boolean> = _isSpeaking.asStateFlow()

    private val _speechTick = MutableStateFlow(0L)
    val speechTick: StateFlow<Long> = _speechTick.asStateFlow()

    /**
     * Emits the original text of any utterance we failed to synthesize —
     * Microsoft 403ing the WebSocket, transient network error, empty MP3
     * body, anything that makes the drain loop drop the sentence.
     * [JarvisTtsEngine] subscribes and re-enqueues the same text onto local
     * Android TTS so the user hears the reply instead of silence.
     */
    private val _failures = MutableSharedFlow<String>(
        replay              = 0,
        extraBufferCapacity = 32,
        onBufferOverflow    = BufferOverflow.DROP_OLDEST,
    )
    val failures: SharedFlow<String> = _failures.asSharedFlow()

    // ── Public API ────────────────────────────────────────────────────────────

    fun enqueue(text: String) {
        if (text.isBlank()) return
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
                val mp3  = synthesize(text)
                if (mp3 == null) {
                    // 403, WebSocket failure, empty body — let the engine
                    // re-route this text to local Android TTS instead of
                    // swallowing it.
                    Log.w(TAG, "synthesize failed for '${text.take(40)}…' → emitting failure for local TTS fallback")
                    _failures.tryEmit(text)
                    continue
                }
                playOne(mp3)
            }
        } finally {
            playerJob = null
            if (queue.isEmpty()) _isSpeaking.value = false
        }
    }

    /**
     * Open a WebSocket, send the config + SSML messages, collect every
     * `Path:audio` binary frame's payload into memory, and return it as a
     * temp MP3 file once the server signals `turn.end`.
     */
    /**
     * Synthesize + play a single sample with a specific voice — used by the
     * Voice Settings "Preview" button. Returns true on success; false when
     * Microsoft 403s (the 30-second confidence fix: user sees the failure
     * immediately, not masked by a silent fallback to local TTS).
     */
    suspend fun preview(text: String, voiceOverride: String): Boolean {
        stop()
        val mp3 = synthesize(text, voiceOverride) ?: return false
        playOne(mp3)
        return true
    }

    private suspend fun synthesize(
        text: String,
        voiceOverride: String? = null,
    ): File? = withContext(Dispatchers.IO) {
        suspendCancellableCoroutine { cont ->
            // Microsoft's read-aloud endpoint anti-abuse stack (matched
            // byte-for-byte to the current `edge-tts` Python library v7+):
            //   1. Sec-MS-GEC token — SHA-256 over a 5-minute-bucketed
            //      Windows FILETIME + the TrustedClientToken constant.
            //   2. Sec-MS-GEC-Version — Edge browser build ID; kept in sync
            //      with the User-Agent's `Edg/…` suffix. MS rotates the
            //      accepted range every few months.
            //   3. ConnectionId — UUID (no dashes) in the query string.
            //      Server uses it to attribute per-connection quotas; older
            //      clients didn't need it, but in 2025 the endpoint started
            //      returning 403 when it was absent on some edge servers.
            //   4. Browser-shape cache / Accept headers — the handshake
            //      pattern-matches against real Edge; stripped-down
            //      requests get 403'd on some POPs.
            val gecToken     = computeSecMsGecToken()
            val connectionId = UUID.randomUUID().toString().replace("-", "")
            val url = WS_ENDPOINT +
                "&Sec-MS-GEC=$gecToken" +
                "&Sec-MS-GEC-Version=$SEC_MS_GEC_VERSION" +
                "&ConnectionId=$connectionId"
            val request = Request.Builder()
                .url(url)
                .header("Pragma",        "no-cache")
                .header("Cache-Control", "no-cache")
                .header("Origin",        "chrome-extension://jdiccldimpdaibmpdkjnbmckianbfold")
                .header("Accept-Encoding", "gzip, deflate, br")
                .header("Accept-Language", "en-US,en;q=0.9")
                .header("User-Agent",    USER_AGENT)
                .build()

            val mp3Buf = ByteArrayOutputStream()
            // OkHttp serialises WebSocketListener callbacks on a single
            // dispatcher thread, so a plain Boolean is safe here.
            var resumed = false

            val listener = object : WebSocketListener() {
                override fun onOpen(ws: WebSocket, response: Response) {
                    val now = timestamp()
                    // 1) Config frame — tells the server what audio format we want.
                    ws.send(
                        "X-Timestamp:$now\r\n" +
                        "Content-Type:application/json; charset=utf-8\r\n" +
                        "Path:speech.config\r\n\r\n" +
                        CONFIG_JSON
                    )
                    // 2) SSML frame — the actual thing to speak, wrapped in
                    //    the voice selector.
                    val requestId = UUID.randomUUID().toString().replace("-", "")
                    val ssml = buildSsml(voiceOverride ?: voice, text)
                    ws.send(
                        "X-RequestId:$requestId\r\n" +
                        "Content-Type:application/ssml+xml\r\n" +
                        "X-Timestamp:$now\r\n" +
                        "Path:ssml\r\n\r\n" +
                        ssml
                    )
                }

                override fun onMessage(ws: WebSocket, text: String) {
                    // Text frames mark lifecycle — we only care about turn.end
                    // to know when to stop listening for audio chunks.
                    if (text.contains("Path:turn.end")) {
                        ws.close(1000, "done")
                        if (!resumed) {
                            resumed = true
                            val bytes = mp3Buf.toByteArray()
                            val file = if (bytes.isNotEmpty()) writeTempMp3(bytes) else null
                            cont.resume(file)
                        }
                    }
                }

                override fun onMessage(ws: WebSocket, bytes: ByteString) {
                    // Binary frame: [uint16 headerLen][ASCII header][payload].
                    val raw = bytes.toByteArray()
                    if (raw.size < 2) return
                    val headerLen = ((raw[0].toInt() and 0xFF) shl 8) or (raw[1].toInt() and 0xFF)
                    val audioStart = 2 + headerLen
                    if (audioStart >= raw.size) return
                    val header = String(raw, 2, headerLen, Charsets.US_ASCII)
                    if ("Path:audio" in header) {
                        mp3Buf.write(raw, audioStart, raw.size - audioStart)
                    }
                }

                override fun onFailure(ws: WebSocket, t: Throwable, r: Response?) {
                    Log.w(TAG, "Edge TTS websocket failed: ${t.message}")
                    if (!resumed) {
                        resumed = true
                        cont.resume(null)
                    }
                }

                override fun onClosed(ws: WebSocket, code: Int, reason: String) {
                    if (!resumed) {
                        resumed = true
                        val bytes = mp3Buf.toByteArray()
                        val file = if (bytes.isNotEmpty()) writeTempMp3(bytes) else null
                        cont.resume(file)
                    }
                }
            }

            val ws = httpClient.newWebSocket(request, listener)
            cont.invokeOnCancellation { runCatching { ws.cancel() } }
        }
    }

    private fun writeTempMp3(bytes: ByteArray): File {
        val tmp = File.createTempFile("edge-tts-", ".mp3", context.cacheDir)
        tmp.outputStream().use { it.write(bytes) }
        return tmp
    }

    // ── Playback ──────────────────────────────────────────────────────────────

    private suspend fun playOne(mp3: File) = withContext(Dispatchers.Main) {
        // Mirror the GroqTtsClient fix: try voice-comm (for Samsung AEC),
        // fall back to MEDIA if the device's voice-comm pipeline rejects
        // the audio format. See GroqTtsClient.playOne for the full story.
        fun buildPlayer(usage: Int): MediaPlayer = MediaPlayer().apply {
            setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(usage)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            setDataSource(mp3.absolutePath)
            prepare()
        }
        val mp: MediaPlayer = runCatching {
            buildPlayer(AudioAttributes.USAGE_VOICE_COMMUNICATION)
        }.getOrElse { e1 ->
            Log.w(TAG, "MediaPlayer voice-comm prepare failed (${e1.message}); retrying with MEDIA")
            runCatching { buildPlayer(AudioAttributes.USAGE_MEDIA) }.getOrElse { e2 ->
                Log.w(TAG, "MediaPlayer MEDIA prepare also failed: ${e2.message}")
                runCatching { mp3.delete() }
                return@withContext
            }
        }
        current = mp

        // Per-word tick approximation so the voice-overlay glow animates.
        tickJob = scope.launch {
            while (mp.isPlaying || _isSpeaking.value) {
                _speechTick.value = System.currentTimeMillis()
                delay(140)
            }
        }

        mp.start()
        // Block this coroutine until playback completes or is cancelled.
        suspendCancellableCoroutine { cont ->
            mp.setOnCompletionListener {
                runCatching { mp.release() }
                current = null
                tickJob?.cancel()
                mp3.delete()
                if (queue.isEmpty()) _isSpeaking.value = false
                cont.resume(Unit)
            }
            cont.invokeOnCancellation {
                runCatching { mp.stop() }
                runCatching { mp.release() }
                mp3.delete()
            }
        }
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private fun buildSsml(voice: String, text: String): String {
        // Minimal XML escaping — the user's message text can contain <, >, &.
        val safe = text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        return "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' " +
               "xml:lang='en-US'><voice name='$voice'>" +
               "<prosody pitch='+0Hz' rate='+0%' volume='+0%'>$safe</prosody>" +
               "</voice></speak>"
    }

    private fun timestamp(): String {
        val fmt = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US)
        fmt.timeZone = TimeZone.getTimeZone("UTC")
        return fmt.format(Date())
    }

    /**
     * Computes the Sec-MS-GEC anti-abuse token Microsoft now requires on
     * the Edge read-aloud WebSocket. Matches the Python `edge-tts` library
     * byte-for-byte:
     *
     *   ticks = (seconds since Unix epoch + Windows-to-Unix offset) * 10^7
     *   ticks -= ticks mod 3e9        # round down to 5-minute window
     *   token = SHA256(f"{ticks}{TRUSTED_TOKEN}").uppercase()
     *
     * The offset `11644473600` is the seconds between 1601-01-01 (Windows
     * FILETIME epoch) and 1970-01-01 (Unix epoch). The result is a Windows
     * FILETIME in hundreds of nanoseconds, bucketed to a 5-minute window
     * (so multiple requests within 5 min share the same token — the server
     * accepts the previous and current window so clock skew up to ~5 min
     * is tolerated). Uppercase hex output is what the server expects.
     */
    private fun computeSecMsGecToken(): String {
        val windowsToUnixOffsetSec = 11_644_473_600L
        val nowSec  = System.currentTimeMillis() / 1_000L
        val ticks   = (nowSec + windowsToUnixOffsetSec) * 10_000_000L
        val bucket  = ticks - (ticks % 3_000_000_000L)
        val digest  = MessageDigest.getInstance("SHA-256")
            .digest("$bucket$TRUSTED_TOKEN".toByteArray(Charsets.US_ASCII))
        return digest.joinToString("") { "%02X".format(it) }
    }

    companion object {
        private const val TAG = "EdgeTtsClient"

        // Token hard-coded in the Edge browser binary. Public, unchanging,
        // used by every `edge-tts` Python library in the wild.
        private const val TRUSTED_TOKEN =
            "6A5AA1D4EAFF4E9FB37E23D68491D6F4"
        private const val WS_ENDPOINT =
            "wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1" +
                "?TrustedClientToken=$TRUSTED_TOKEN"
        private const val USER_AGENT =
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.2903.99"

        /**
         * Bumps together with the User-Agent's `Edg/…` suffix. Microsoft's
         * endpoint uses this to decide which Sec-MS-GEC algorithm version
         * it expects. Microsoft rotates the accepted version range every
         * few months — if this starts 403ing again, bump to the current
         * `Edg/…` version reported by `edge-tts` Python on PyPI.
         */
        private const val SEC_MS_GEC_VERSION = "1-131.0.2903.99"

        // Config JSON that tells the server to return 24 kHz 48 kbit/s mono
        // MP3 chunks + word-boundary metadata. MP3 plays directly through
        // MediaPlayer without re-encoding.
        private const val CONFIG_JSON = """{"context":{"synthesis":{"audio":{"metadataoptions":{"sentenceBoundaryEnabled":"false","wordBoundaryEnabled":"true"},"outputFormat":"audio-24khz-48kbitrate-mono-mp3"}}}}"""
    }
}
