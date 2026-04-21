package com.jarvis.android.presentation.chat

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlin.math.abs
import kotlin.math.min

/**
 * Real-time mic amplitude monitor. Reads 16-bit PCM from [AudioRecord]
 * in a background coroutine and publishes a smoothed peak amplitude in
 * [0, 1] via [level].
 *
 * Use this — not [AudioAmplitudeMonitor] — when you want to reflect
 * what the USER is saying. The old Visualizer-based monitor captures
 * the speaker output, so it shows nothing when the user talks.
 *
 * Requires [Manifest.permission.RECORD_AUDIO]. Lifecycle:
 *   val mic = MicAmplitudeMonitor(context)
 *   mic.start(scope)
 *   // observe mic.level
 *   mic.stop()
 *
 * Start/stop are safe to call multiple times.
 */
class MicAmplitudeMonitor(private val context: Context) {

    private val _level = MutableStateFlow(0f)
    /** Smoothed peak amplitude, 0 = silence, 1 = loud speech / shout. */
    val level: StateFlow<Float> = _level.asStateFlow()

    private var recorder: AudioRecord? = null
    private var job: Job? = null

    /** True iff RECORD_AUDIO is granted right now. */
    private fun hasPermission(): Boolean =
        context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED

    @SuppressLint("MissingPermission") // guarded by hasPermission()
    fun start(scope: CoroutineScope) {
        if (job?.isActive == true) return
        if (!hasPermission()) {
            Log.w(TAG, "RECORD_AUDIO not granted — mic monitor disabled")
            return
        }
        val minBuf = AudioRecord.getMinBufferSize(
            SAMPLE_RATE, CHANNEL, ENCODING,
        ).coerceAtLeast(2048)

        try {
            recorder = AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION,
                SAMPLE_RATE, CHANNEL, ENCODING, minBuf * 2,
            ).apply {
                if (state != AudioRecord.STATE_INITIALIZED) {
                    Log.w(TAG, "AudioRecord failed to initialize")
                    release()
                    recorder = null
                    return
                }
                startRecording()
            }
        } catch (e: Exception) {
            Log.w(TAG, "AudioRecord start failed: ${e.message}")
            recorder = null
            return
        }

        val buf = ShortArray(minBuf)
        var smoothed = 0f

        job = scope.launch(Dispatchers.IO) {
            val rec = recorder ?: return@launch
            while (isActive) {
                val n = try {
                    rec.read(buf, 0, buf.size)
                } catch (e: Exception) {
                    Log.w(TAG, "AudioRecord read failed: ${e.message}")
                    break
                }
                if (n <= 0) continue
                // Peak amplitude over the chunk, normalised to [0,1].
                var peak = 0
                val take = min(n, buf.size)
                for (i in 0 until take) {
                    val s = abs(buf[i].toInt())
                    if (s > peak) peak = s
                }
                val norm = (peak / 32768f).coerceIn(0f, 1f)
                // Exponential smoothing: quick attack, gentle decay.
                val alpha = if (norm > smoothed) ATTACK else DECAY
                smoothed = alpha * norm + (1f - alpha) * smoothed
                _level.value = smoothed
            }
        }
        Log.d(TAG, "mic monitor started (buf=$minBuf samples)")
    }

    fun stop() {
        job?.cancel()
        job = null
        recorder?.apply {
            try {
                if (state == AudioRecord.STATE_INITIALIZED) stop()
                release()
            } catch (_: Exception) { /* ignore */ }
        }
        recorder = null
        _level.value = 0f
        Log.d(TAG, "mic monitor stopped")
    }

    companion object {
        private const val SAMPLE_RATE = 16_000
        private const val CHANNEL = AudioFormat.CHANNEL_IN_MONO
        private const val ENCODING = AudioFormat.ENCODING_PCM_16BIT
        private const val ATTACK = 0.45f
        private const val DECAY = 0.08f
        private const val TAG = "MicAmplitudeMonitor"
    }
}
