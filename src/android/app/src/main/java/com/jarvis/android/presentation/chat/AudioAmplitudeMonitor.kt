package com.jarvis.android.presentation.chat

import android.media.audiofx.Visualizer
import android.util.Log
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlin.math.sqrt

/**
 * Captures real-time audio amplitude from the device's primary output mix using
 * the Android [Visualizer] API and maps it to [BAR_COUNT] normalised bar heights.
 *
 * ## How it works
 * [Visualizer] with `audioSessionId = 0` attaches to the **master audio mix** —
 * all audio currently playing (TTS, media, etc.) is captured. The raw waveform
 * (unsigned 8-bit PCM, 128 = silence) is divided into [BAR_COUNT] equal segments;
 * the RMS energy of each segment becomes that bar's raw amplitude.
 *
 * Exponential smoothing (`α = [ALPHA]`) is applied per-bar so the visualisation
 * stays fluid rather than jittery.
 *
 * ## Fallback
 * If [Visualizer] is unavailable (permission denied, hardware limitation, or
 * Android version restriction) [amplitudes] stays at all-zeros and the call-site
 * should fall back to a simulated waveform.
 *
 * ## Lifecycle
 * Call [start] when the voice screen becomes visible and [stop] when it is
 * disposed. Multiple [start]/[stop] cycles are safe.
 *
 * Requires: `android.permission.RECORD_AUDIO`
 */
class AudioAmplitudeMonitor {

    private val _amplitudes = MutableStateFlow(FloatArray(BAR_COUNT))

    /**
     * Per-bar normalised amplitude in [0, 1].
     * Updated at [Visualizer.getMaxCaptureRate] / 2 Hz (typically ~20 fps).
     */
    val amplitudes: StateFlow<FloatArray> = _amplitudes.asStateFlow()

    private var visualizer: Visualizer? = null
    private val smoothed   = FloatArray(BAR_COUNT)
    private var running    = false

    // ── Lifecycle ─────────────────────────────────────────────────────────

    fun start() {
        if (running) return
        running = true
        try {
            visualizer = Visualizer(/* audioSessionId = primary mix */ 0).apply {
                // Use the maximum capture size available (typically 1 024 bytes)
                captureSize = Visualizer.getCaptureSizeRange()[1]

                setDataCaptureListener(
                    object : Visualizer.OnDataCaptureListener {
                        override fun onWaveFormDataCapture(
                            v: Visualizer,
                            waveform: ByteArray,
                            samplingRate: Int,
                        ) { processPcm(waveform) }

                        override fun onFftDataCapture(
                            v: Visualizer,
                            fft: ByteArray,
                            samplingRate: Int,
                        ) { /* unused — waveform is sufficient for bar visualisation */ }
                    },
                    /* rate = */ Visualizer.getMaxCaptureRate() / 2,
                    /* waveform = */ true,
                    /* fft = */ false,
                )
                enabled = true
            }
            Log.d(TAG, "Visualizer started (captureSize=${visualizer?.captureSize})")
        } catch (e: Exception) {
            // Fails gracefully — callers fall back to simulated waveform
            Log.w(TAG, "Visualizer unavailable: ${e.message}")
            running = false
        }
    }

    fun stop() {
        running = false
        visualizer?.apply {
            try {
                enabled = false
                release()
            } catch (_: Exception) { /* ignore errors on release */ }
        }
        visualizer = null
        // Clear bars so the screen shows idle state immediately
        _amplitudes.value = FloatArray(BAR_COUNT)
        smoothed.fill(0f)
        Log.d(TAG, "Visualizer stopped")
    }

    // ── PCM → bar amplitudes ──────────────────────────────────────────────

    /**
     * Divides [waveform] into [BAR_COUNT] equal segments, computes the RMS
     * energy of each segment, normalises to [0, 1], and applies exponential
     * smoothing to avoid jitter.
     *
     * PCM format: unsigned 8-bit (byte), where 128 = silence.
     * RMS target range: 0 (silent) → ~50–80 (typical speech peak out of 128).
     */
    private fun processPcm(waveform: ByteArray) {
        val segSize = waveform.size / BAR_COUNT
        val result  = FloatArray(BAR_COUNT)

        for (bar in 0 until BAR_COUNT) {
            val start = bar * segSize
            val end   = minOf(start + segSize, waveform.size)

            var sumSq = 0.0
            for (j in start until end) {
                val sample = (waveform[j].toInt() and 0xFF) - 128   // signed: -128..127
                sumSq += sample.toLong() * sample
            }
            val rms        = sqrt(sumSq / (end - start)).toFloat()
            val normalized = (rms / SPEECH_PEAK_RMS).coerceIn(0f, 1f)

            // Exponential moving average — attack is fast, decay is slightly slower
            val alpha    = if (normalized > smoothed[bar]) ALPHA_ATTACK else ALPHA_DECAY
            smoothed[bar] = alpha * normalized + (1f - alpha) * smoothed[bar]
            result[bar]   = smoothed[bar]
        }

        _amplitudes.value = result
    }

    companion object {
        /** Number of visualisation bars exposed to the UI. */
        const val BAR_COUNT = 28

        /** RMS value that maps to 1.0 (typical peak for speech). */
        private const val SPEECH_PEAK_RMS = 45f

        /** Smoothing: how fast bars rise (attack) and fall (decay). */
        private const val ALPHA_ATTACK = 0.40f
        private const val ALPHA_DECAY  = 0.20f

        private const val TAG = "AudioAmplitudeMonitor"
    }
}
