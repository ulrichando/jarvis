package com.jarvis.android.system.cyber

import android.util.Log
import com.jarvis.android.domain.model.LogEntry
import com.jarvis.android.domain.model.LogLevel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Tails the Android logcat stream and emits parsed [LogEntry] objects.
 *
 * Runs `logcat -v threadtime` as a subprocess and parses each line.
 * The flow never completes — cancel the collection coroutine to stop.
 *
 * Optional [filterTag] and [minLevel] narrow the output.
 *
 * ## Security-relevant patterns
 *
 * Entries are automatically flagged when they match [SECURITY_PATTERNS] —
 * keywords that often indicate root activity, permission grants, SELinux
 * denials, crash loops, or network anomalies.
 */
@Singleton
class LogWatcher @Inject constructor() {

    /**
     * Stream logcat as a cold [Flow<LogEntry>].
     *
     * @param filterTag  only emit entries whose tag contains this string (case-insensitive)
     * @param minLevel   skip entries below this severity
     * @param bufferSize ring buffer size passed to logcat (`-T <N>`)
     */
    fun watch(
        filterTag:  String?   = null,
        minLevel:   LogLevel  = LogLevel.DEBUG,
        bufferSize: Int       = 1000,
    ): Flow<LogEntry> = flow {
        val cmd = buildList {
            add("logcat"); add("-v"); add("threadtime")
            add("-T"); add("$bufferSize")
            if (minLevel.logcatChar != null) add("*:${minLevel.logcatChar}")
        }.toTypedArray()

        val process = try {
            Runtime.getRuntime().exec(cmd)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start logcat", e)
            return@flow
        }

        try {
            process.inputStream.bufferedReader().use { reader ->
                var line: String?
                while (reader.readLine().also { line = it } != null) {
                    val entry = parseLine(line!!) ?: continue
                    if (filterTag != null && !entry.tag.contains(filterTag, ignoreCase = true)) continue
                    emit(entry)
                }
            }
        } finally {
            process.destroy()
        }
    }.flowOn(Dispatchers.IO)

    // ── Parsing ───────────────────────────────────────────────────────────────

    /**
     * Parse a `threadtime` logcat line:
     * `MM-DD HH:MM:SS.mmm  PID   TID LEVEL TAG  : message`
     */
    private fun parseLine(raw: String): LogEntry? {
        // Fast pre-check: threadtime lines are at least 24 chars
        if (raw.length < 24) return null

        return try {
            // Example: "04-11 14:23:01.234  1234  5678 E SomeTag : something happened"
            val parts = raw.split(Regex("\\s+"), limit = 7)
            if (parts.size < 6) return null

            val levelChar = parts[4].firstOrNull() ?: return null
            val level = LEVEL_MAP[levelChar] ?: LogLevel.UNKNOWN
            val tag   = parts[5].trimEnd(':').trim()
            val msg   = if (parts.size > 6) parts[6].removePrefix(": ").trim() else ""

            LogEntry(
                timestampMs = System.currentTimeMillis(),   // approx; parsing logcat timestamp is fiddly
                level       = level,
                tag         = tag,
                message     = msg,
                raw         = raw,
            )
        } catch (_: Exception) { null }
    }

    companion object {
        private const val TAG = "LogWatcher"

        private val LEVEL_MAP = mapOf(
            'V' to LogLevel.VERBOSE,
            'D' to LogLevel.DEBUG,
            'I' to LogLevel.INFO,
            'W' to LogLevel.WARN,
            'E' to LogLevel.ERROR,
            'F' to LogLevel.FATAL,
        )

        /** Security-relevant keywords — entries matching these are highlighted in the UI. */
        val SECURITY_PATTERNS = listOf(
            "selinux", "avc:  denied", "su request", "permission denied",
            "root access", "superuser", "magisk", "granted to",
            "FATAL EXCEPTION", "ANR in", "am_crash",
            "network_blocked", "cleartext", "untrusted cert",
            "ssl error", "certificate", "key install",
        )
    }
}

// ── Extension ─────────────────────────────────────────────────────────────────

private val LogLevel.logcatChar: String?
    get() = when (this) {
        LogLevel.VERBOSE -> "V"
        LogLevel.DEBUG   -> "D"
        LogLevel.INFO    -> "I"
        LogLevel.WARN    -> "W"
        LogLevel.ERROR   -> "E"
        LogLevel.FATAL   -> "F"
        LogLevel.UNKNOWN -> null
    }
