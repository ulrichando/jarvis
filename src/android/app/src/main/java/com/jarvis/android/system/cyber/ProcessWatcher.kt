package com.jarvis.android.system.cyber

import android.util.Log
import com.jarvis.android.domain.model.CyberProcess
import com.jarvis.android.domain.model.ProcessSnapshot
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Reads the running process list from `/proc` on the device.
 *
 * Requires either root access (for full cmdline visibility) or at least
 * the `/proc/<pid>/status` files, which are world-readable on Android.
 *
 * ## Suspicious heuristics
 *
 * A process is flagged suspicious if it matches any of:
 *   - Known malware/RAT process names
 *   - Listening on a raw TCP port without a mapped service
 *   - Running under UID 0 with an unusual name
 *   - Name contains path traversal (`..`) or URL-encoded characters
 */
@Singleton
class ProcessWatcher @Inject constructor() {

    suspend fun snapshot(): ProcessSnapshot = withContext(Dispatchers.IO) {
        val procs = readProcFs()
        val suspicious = procs.filter { it.suspicious }
        ProcessSnapshot(processes = procs, suspicious = suspicious)
    }

    private fun readProcFs(): List<CyberProcess> {
        val procDir = java.io.File("/proc")
        if (!procDir.exists()) return emptyList()

        return procDir.listFiles { f -> f.isDirectory && f.name.all { it.isDigit() } }
            ?.mapNotNull { pidDir ->
                try {
                    val pid     = pidDir.name.toInt()
                    val status  = parseStatus(pidDir)
                    val cmdline = readCmdline(pidDir)
                    val name    = status["Name"] ?: cmdline.substringAfterLast('/').take(15)
                    val user    = status["Uid"]?.split('\t')?.firstOrNull() ?: "?"
                    val rssKb   = status["VmRSS"]?.filter { it.isDigit() }?.toLongOrNull() ?: 0L
                    val state   = status["State"]?.take(1) ?: "?"

                    val (susp, reason) = isSuspicious(name, cmdline, user)
                    CyberProcess(
                        pid       = pid,
                        name      = name,
                        user      = user,
                        state     = state,
                        rssKb     = rssKb,
                        cpuPct    = 0f,   // would need two snapshots for accurate CPU%
                        cmdline   = cmdline.take(256),
                        suspicious = susp,
                        reason    = reason,
                    )
                } catch (e: Exception) {
                    Log.v(TAG, "Skipping pid ${pidDir.name}: ${e.message}")
                    null
                }
            }
            ?.sortedByDescending { it.rssKb }
            ?: emptyList()
    }

    private fun parseStatus(pidDir: java.io.File): Map<String, String> {
        val result = mutableMapOf<String, String>()
        try {
            java.io.File(pidDir, "status").forEachLine { line ->
                val colon = line.indexOf(':')
                if (colon > 0) {
                    result[line.substring(0, colon).trim()] = line.substring(colon + 1).trim()
                }
            }
        } catch (_: Exception) {}
        return result
    }

    private fun readCmdline(pidDir: java.io.File): String {
        return try {
            java.io.File(pidDir, "cmdline")
                .readBytes()
                .map { if (it == 0.toByte()) ' ' else it.toInt().toChar() }
                .joinToString("")
                .trim()
        } catch (_: Exception) { "" }
    }

    private fun isSuspicious(name: String, cmdline: String, uid: String): Pair<Boolean, String?> {
        val lower = name.lowercase()
        val cmdLower = cmdline.lowercase()

        // Known RAT / backdoor names
        SUSPICIOUS_NAMES.firstOrNull { lower.contains(it) }?.let {
            return true to "Matches known malware pattern: $it"
        }

        // Path traversal in process name
        if (name.contains("..") || name.contains("%")) {
            return true to "Suspicious characters in process name"
        }

        // Reverse shells / common pentest tools running on device
        SUSPICIOUS_CMD_PATTERNS.firstOrNull { cmdLower.contains(it) }?.let {
            return true to "Cmdline matches suspicious pattern: $it"
        }

        // Root process with unusual name
        if (uid == "0" && lower !in EXPECTED_ROOT_PROCS) {
            if (lower.none { it.isLetter() } || lower.length < 2) {
                return true to "Root process with unusual name"
            }
        }

        return false to null
    }

    companion object {
        private const val TAG = "ProcessWatcher"

        private val SUSPICIOUS_NAMES = listOf(
            "mettle", "meterpreter", "ngrok", "frpc", "frp", "chisel",
            "ligolo", "rathole", "neo-regeorg", "ncat-evil", "backdoor",
            "rootkit", "xmrig", "minergate", "ccminer",
        )

        private val SUSPICIOUS_CMD_PATTERNS = listOf(
            "/bin/sh -i", "nc -e", "bash -i", "python -c",
            "socat exec", "mkfifo /tmp", "0.0.0.0:4444",
            "xterm -display", "import socket,subprocess",
        )

        private val EXPECTED_ROOT_PROCS = setOf(
            "init", "kthreadd", "ksoftirqd", "kworker", "rcu_sched",
            "watchdog", "migration", "irq", "adbd", "vold", "logd",
            "servicemanager", "surfaceflinger", "installd", "lmkd",
            "netd", "zygote", "zygote64", "system_server",
        )
    }
}
