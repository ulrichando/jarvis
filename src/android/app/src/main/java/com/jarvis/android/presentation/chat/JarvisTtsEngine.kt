package com.jarvis.android.presentation.chat

import android.content.Context
import android.media.AudioAttributes
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.speech.tts.Voice
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
    private val groqTts:        GroqTtsClient,
    private val edgeTts:        EdgeTtsClient,
) {

    // State flows declared *before* the init block that subscribes to
    // upstream TTS client flows. Kotlin property initialisers run in
    // declaration order, interleaved with init blocks; putting these up
    // top guarantees they are non-null by the time any launched coroutine
    // in [init] executes. (An earlier iteration had these below the init
    // block, and the very first StateFlow emission on Main.immediate
    // dereferenced them before construction finished → NPE → app crash
    // at launch. Keep the declarations here.)
    private val _isSpeaking = MutableStateFlow(false)
    val isSpeaking: StateFlow<Boolean> = _isSpeaking.asStateFlow()

    private val _speechTick = MutableStateFlow(0L)
    val speechTick: StateFlow<Long> = _speechTick.asStateFlow()

    // Mirror the active backend's flows into our own so the rest of the app
    // can observe a single isSpeaking / speechTick regardless of which
    // backend is actually speaking. Both Brain and Edge mirror into the
    // same state; whichever is talking bumps it.
    private val mirrorScope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)

    init {
        mirrorScope.launch {
            brainTts.isSpeaking.collectLatest { speaking ->
                if (speaking) _isSpeaking.value = true
            }
        }
        mirrorScope.launch {
            brainTts.speechTick.collectLatest { t ->
                if (t > 0L) _speechTick.value = t
            }
        }
        mirrorScope.launch {
            edgeTts.isSpeaking.collectLatest { speaking ->
                if (speaking) _isSpeaking.value = true
            }
        }
        mirrorScope.launch {
            edgeTts.speechTick.collectLatest { t ->
                if (t > 0L) _speechTick.value = t
            }
        }
        mirrorScope.launch {
            groqTts.isSpeaking.collectLatest { speaking ->
                if (speaking) _isSpeaking.value = true
            }
        }
        mirrorScope.launch {
            groqTts.speechTick.collectLatest { t ->
                if (t > 0L) _speechTick.value = t
            }
        }

        // ── Cross-backend fallback ────────────────────────────────────────
        // When a cloud backend fails an utterance (Groq model decommissioned
        // / key expired, Edge endpoint 403s, network flaky), it emits the
        // dropped text on its `failures` flow. We do two things:
        //
        //  1. Flip `activeBackend` to LOCAL for the rest of THIS turn, so
        //     any further enqueue()d sentences skip the broken cloud path
        //     entirely. Previously failures were per-utterance, which meant
        //     sentence N's local fallback could play at the same time as
        //     sentence N+1's successful cloud playback — the user heard
        //     two voices overlapping. Sticky degrade keeps it single-voice.
        //
        //  2. Stop the cloud backend immediately so any sentences still in
        //     its internal queue don't turn into a third concurrent stream.
        //
        // activeBackend resets on the next speak() call (= next turn), so
        // recovery is automatic: Groq is retried on the next user turn.
        mirrorScope.launch {
            groqTts.failures.collect { text ->
                Log.w(TAG, "Groq failure → sticky-degrade to LOCAL for rest of turn; speaking '${text.take(40)}…'")
                activeBackend = Backend.LOCAL
                runCatching { groqTts.stop() }
                speakLocal(text, flush = false)
            }
        }
        mirrorScope.launch {
            edgeTts.failures.collect { text ->
                Log.w(TAG, "Edge failure → sticky-degrade to LOCAL for rest of turn; speaking '${text.take(40)}…'")
                activeBackend = Backend.LOCAL
                runCatching { edgeTts.stop() }
                speakLocal(text, flush = false)
            }
        }

        // Pre-warm the local Android TTS engine so the fallback path is
        // instant when we need it. Without this, the first failed cloud
        // utterance pays a 1–2s cold-start cost on Google TTS — long enough
        // that the user types again, thinks the app is broken, or scrolls
        // away. `ensureReady { }` runs the init off the main thread and
        // leaves `isReady = true` for the next speak/enqueue.
        ensureReady { Log.i(TAG, "Local TTS pre-warmed for fallback path") }
    }

    /** True when the brain-server TTS is explicitly configured. */
    private fun useBrainTts(): Boolean = apiKeyProvider.getBrainTtsUrl().isNotBlank()

    /**
     * Groq PlayAI TTS — primary path. Same API key the user already set
     * up for chat, standard OpenAI-compatible POST to /v1/audio/speech,
     * returns WAV. This is the same stack Groq's own Playground uses; it
     * doesn't have the Sec-MS-GEC / regional-POP gating problems that
     * Microsoft's Edge Read-Aloud endpoint keeps throwing at us.
     *
     * Enabled by default when the user has a Groq key configured; a
     * Settings toggle lets them turn it off explicitly.
     */
    private fun useGroqTts(): Boolean =
        !useBrainTts() &&
            groqTts.hasCredentials() &&
            apiKeyProvider.isGroqTtsEnabled()

    /**
     * Edge TTS — opt-in fallback. Microsoft's public endpoint sporadically
     * 403s even with the correct handshake, so this is only used when the
     * user explicitly flips on the Settings toggle and Groq isn't wired.
     */
    private fun useEdgeTts(): Boolean =
        !useBrainTts() &&
            !useGroqTts() &&
            apiKeyProvider.isEdgeTtsEnabled()

    private var tts: TextToSpeech? = null
    private var isReady = false

    // _isSpeaking and _speechTick are declared at the top of the class so
    // they're initialised before the init block's coroutines dereference
    // them. See the comment there.

    // Off by default — see ChatUiState.ttsEnabled. Voice mode flips this on
    // explicitly when the user opens the voice overlay.
    private var enabled = false

    fun setEnabled(value: Boolean) {
        enabled = value
        if (!value) stop()
    }

    fun isEnabled(): Boolean = enabled

    /**
     * Which backend owns audio output for the current turn. Set at [speak]
     * time from the user's settings, and pinned for the rest of the turn
     * so subsequent [enqueue]d sentences go to the same backend. If the
     * chosen cloud backend fails on any sentence in the turn, the engine
     * flips this to LOCAL (sticky) for every remaining sentence — prevents
     * the dual-audio bug where a later sentence succeeded on cloud while
     * the failed sentence's local fallback was still speaking.
     */
    private enum class Backend { BRAIN, GROQ, EDGE, LOCAL }
    @Volatile private var activeBackend: Backend = Backend.LOCAL

    /**
     * True once the current turn's backend has been resolved. Chat streaming
     * calls [enqueue] for every sentence but never [speak], so the backend
     * must be picked on the first enqueue of each turn — otherwise we stay
     * on [Backend.LOCAL] (the default) and the user's Edge/Groq voice toggle
     * is ignored. Reset on [stop] so the next reply resolves fresh.
     */
    @Volatile private var turnInitialized: Boolean = false

    /**
     * Stop every backend's in-flight playback + queue. Called at the top
     * of [speak] (a new turn) and whenever we pivot between backends mid-
     * turn. Crucial for the "only one TTS active at a time" invariant —
     * without it, a previous turn's Edge MediaPlayer could still be
     * draining while a new Groq turn starts, producing overlapping audio
     * and occasional `MediaPlayer.prepare()` crashes from resource
     * contention.
     */
    private fun stopAllBackends() {
        runCatching { brainTts.stop() }
        runCatching { groqTts.stop() }
        runCatching { edgeTts.stop() }
        runCatching { tts?.stop() }
    }

    private fun resolveBackend(): Backend = when {
        useBrainTts() -> Backend.BRAIN
        useGroqTts()  -> Backend.GROQ
        useEdgeTts()  -> Backend.EDGE
        else          -> Backend.LOCAL
    }

    /** Speak [text] if TTS is enabled. Interrupts any in-progress speech. */
    fun speak(text: String) {
        if (!enabled) {
            Log.d(TAG, "speak() ignored — TTS disabled (text len=${text.length})")
            return
        }
        if (text.isBlank()) return
        // A new turn — flush every backend before starting a fresh one.
        // Without this, a previous backend's drain can still be running
        // and produce overlapping audio with the new one.
        stopAllBackends()
        activeBackend   = resolveBackend()
        turnInitialized = true
        Log.i(TAG, "speak() backend=$activeBackend text-len=${text.length} preview='${text.take(40)}…'")
        when (activeBackend) {
            Backend.BRAIN -> brainTts.speak(apiKeyProvider.getBrainTtsUrl(), text)
            Backend.GROQ  -> groqTts.speak(text)
            Backend.EDGE  -> edgeTts.speak(text)
            Backend.LOCAL -> speakLocal(text, flush = true)
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
        // First sentence of a new turn: resolve the backend from the current
        // user preferences. Streaming chat calls enqueue() for every
        // sentence but never speak(); without this we'd stay on the default
        // Backend.LOCAL forever and the Edge/Groq voice toggle would be
        // silently ignored. Subsequent sentences in the same turn stay
        // pinned so they don't split across backends mid-reply.
        if (!turnInitialized) {
            activeBackend   = resolveBackend()
            turnInitialized = true
            Log.i(TAG, "enqueue() turn init backend=$activeBackend")
        }
        Log.i(TAG, "enqueue() backend=$activeBackend text-len=${text.length} preview='${text.take(40)}…'")
        when (activeBackend) {
            Backend.BRAIN -> brainTts.enqueue(apiKeyProvider.getBrainTtsUrl(), text)
            Backend.GROQ  -> groqTts.enqueue(text)
            Backend.EDGE  -> edgeTts.enqueue(text)
            Backend.LOCAL -> speakLocal(text, flush = false)
        }
    }

    /**
     * Route [text] to Android's built-in [TextToSpeech]. Used as (a) the
     * default when no cloud backend is configured, (b) the fallback
     * destination when a cloud backend emits on its `failures` flow.
     *
     * Always callable — the engine pre-warms in [init] so there's no cold
     * start. [flush] = true interrupts any in-progress utterance (for
     * barge-in on voice mode); [flush] = false appends to the local queue
     * so chained fallback sentences stay in order.
     *
     * Enabled-check is intentionally skipped: failure callbacks fire from
     * the cloud client's own drain loop which already passed the enabled
     * gate when the utterance was originally accepted. Re-checking here
     * would race with the user toggling TTS off mid-reply and swallow the
     * fallback silently.
     */
    private fun speakLocal(text: String, flush: Boolean) {
        if (text.isBlank()) return
        _isSpeaking.value = true
        ensureReady {
            val utteranceId = UUID.randomUUID().toString()
            val mode = if (flush) TextToSpeech.QUEUE_FLUSH else TextToSpeech.QUEUE_ADD
            val result = tts?.speak(text, mode, null, utteranceId)
            Log.i(TAG, "local tts.speak() result=$result flush=$flush utt=$utteranceId")
        }
    }

    /**
     * Preview a specific voice from a specific backend — plays a short
     * sample sentence immediately, bypassing the streaming queue. Used by
     * the Voice Settings Preview buttons. Returns true on success, false
     * if the backend refused (e.g. Edge 403, Groq 400, missing key).
     *
     * This is the industry-standard pattern: ChatGPT / Claude / Siri all
     * play a sample on voice selection so the user has immediate audible
     * confirmation. Without it, a silent fallback to local TTS makes
     * voice changes feel "stuck" on the local male voice.
     */
    suspend fun previewGroq(voiceId: String): Boolean {
        stopAllBackends()
        return groqTts.preview(PREVIEW_TEXT, voiceId)
    }
    suspend fun previewEdge(voiceId: String): Boolean {
        stopAllBackends()
        return edgeTts.preview(PREVIEW_TEXT, voiceId)
    }

    /** Stop speaking immediately — every backend we might have fired. */
    fun stop() {
        stopAllBackends()
        activeBackend   = Backend.LOCAL
        turnInitialized = false
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
        // Samsung devices ship `com.samsung.SMT` as the default TTS engine.
        // Its voice catalogue is narrower and doesn't include a distinct
        // male voice on every locale — so when Google TTS
        // (`com.google.android.tts`) is also installed we prefer that one.
        // Google's en-US male voice (`en-us-x-iom-*`) is predictable across
        // devices. If neither detection works we fall back to the system
        // default (null engine id) so the app still speaks, just possibly
        // in the default female voice.
        val preferredEngine = resolvePreferredEngine()
        val listener = TextToSpeech.OnInitListener { status ->
            if (status == TextToSpeech.SUCCESS) {
                tts?.language = Locale.US
                // Route local TTS output through the voice-communication
                // audio path, same as our cloud TTS MediaPlayers. Without
                // this, local TTS falls on the MEDIA stream and bypasses
                // Samsung's hardware AEC — so if the mic is hot, it hears
                // JARVIS's own voice and the recognizer transcribes it as
                // a new user prompt. See GroqTtsClient.kt for the longer
                // explanation of why VOICE_COMMUNICATION matters here.
                tts?.setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build(),
                )
                tts?.let { engine ->
                    logAvailableVoices(engine)
                    pickMaleVoice(engine)?.also { v ->
                        Log.i(TAG, "Selected male TTS voice: ${v.name} (engine=${engine.defaultEngine})")
                        engine.voice = v
                    } ?: Log.w(TAG, "No male voice matched — using engine default. " +
                        "Install a male voice: Settings → General management → " +
                        "Text-to-speech output → Speech rate / language → choose male.")
                }
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
                Log.i(TAG, "TTS engine ready (engine=${tts?.defaultEngine})")
            } else {
                Log.e(TAG, "TTS init failed: status=$status")
            }
        }
        tts = if (preferredEngine != null) {
            Log.i(TAG, "Using preferred TTS engine: $preferredEngine")
            TextToSpeech(context, listener, preferredEngine)
        } else {
            TextToSpeech(context, listener)
        }
    }

    /**
     * Prefer Google TTS (`com.google.android.tts`) over the device default
     * when both are installed — its voice catalogue is broader and includes
     * reliably-tagged male voices (`en-us-x-iom-*` etc.) that our picker
     * understands. Returns `null` (= use system default) if Google TTS is
     * not installed.
     */
    private fun resolvePreferredEngine(): String? {
        val pm = context.packageManager
        return runCatching {
            pm.getPackageInfo("com.google.android.tts", 0)
            "com.google.android.tts"
        }.getOrNull()
    }

    /**
     * Log every installed voice once at init so we can see what the picker
     * is choosing from. Helpful on Samsung devices where the voice naming
     * is unusual.
     */
    private fun logAvailableVoices(engine: TextToSpeech) {
        val voices = runCatching { engine.voices }.getOrNull() ?: return
        val english = voices.filter { it.locale?.language == "en" }
        Log.i(TAG, "Available en-* voices (${english.size}):")
        english.take(20).forEach { v ->
            Log.i(TAG, "  - ${v.name} (locale=${v.locale}, network=${v.isNetworkConnectionRequired})")
        }
    }

    /**
     * Pick a male English voice from the installed TTS engine. Android's
     * default Locale.US voice is female on most Samsung / Pixel devices
     * (`en-us-x-tpf-*` / `en-us-x-vmw-*`) so without this step everyone
     * hears a female JARVIS. Male variants on Google TTS are `-iom`, `-iol`,
     * `-sfg`, or `-iod`; other engines just include "male" in the display
     * name. We prefer those, then fall back to any en-US voice whose name
     * doesn't contain "female".
     */
    private fun pickMaleVoice(engine: TextToSpeech): Voice? {
        val voices: Set<Voice> = runCatching { engine.voices }.getOrNull() ?: return null
        val english = voices.filter { v ->
            v.locale?.language == "en" && !v.isNetworkConnectionRequired &&
                v.features?.contains(TextToSpeech.Engine.KEY_FEATURE_NOT_INSTALLED) != true
        }
        // Priority order of Google TTS voice IDs that are reliably MALE on
        // current Samsung / Pixel devices running Android 14+. Empirically:
        //   -iom / -iol / -iod / -iob   → male
        //   -sfg                        → ambiguous on some ROMs (reports
        //                                 as female on recent Galaxy S24
        //                                 builds — dropped from allowlist
        //                                 so we fall through to iob/iol)
        //   -tpf / -vmw                 → female (avoid)
        val malePatterns = listOf("-iom", "-iol", "-iod", "-iob", "-usa-", "-gbc-")
        val femaleHints  = listOf("female", "-tpf", "-vmw", "-sfg", "-sfb", "-gbd-")
        fun looksFemale(v: Voice): Boolean =
            femaleHints.any { it in v.name.lowercase() }
        val byCode = english.firstOrNull { v ->
            !looksFemale(v) && malePatterns.any { it in v.name.lowercase() }
        }
        if (byCode != null) return byCode
        val byWord = english.firstOrNull { v ->
            val n = v.name.lowercase()
            !looksFemale(v) && n.contains("male")
        }
        if (byWord != null) return byWord
        // Last resort — any en voice that isn't a known-female pattern.
        return english.firstOrNull { !looksFemale(it) }
    }

    companion object {
        private const val TAG = "JarvisTtsEngine"
        private const val PREVIEW_TEXT =
            "Hi, I'm Jarvis. This is a preview of the selected voice."
    }
}
