package com.jarvis.android.system.root

import android.util.Log
import com.topjohnwu.superuser.Shell
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Executes shell commands with optional root escalation via libsu.
 *
 * This is the single entry point for ALL shell command execution in JARVIS —
 * both the AI tool dispatcher and use-case layer route through here.
 *
 * Root vs non-root:
 *   - [exec] with [asRoot]=true  → uses [Shell.cmd] on the root shell
 *   - [exec] with [asRoot]=false → uses [Runtime.exec] (userspace)
 *   Non-root fallback ensures features like logcat parsing work without SU.
 *
 * Blocked commands: a small denylist prevents the most destructive operations
 * from being executed silently. Claude's tool dispatcher will ask for
 * confirmation before sending these — this is belt-and-suspenders.
 *
 * Output limits: stdout is capped at [MAX_OUTPUT_LINES] to prevent OOM
 * when a command produces massive output (e.g. `find /`).
 */
@Singleton
class RootShell @Inject constructor(
    private val rootManager: RootManager,
) {

    /**
     * Execute [command] in a shell and return a [ShellResult].
     *
     * @param command  The shell command string (passed to sh -c).
     * @param asRoot   If true and root is available, run via the libsu root shell.
     *                 If true and root is NOT available, falls back to userspace.
     * @param timeoutMs Hard timeout in milliseconds. Default 30 seconds.
     *                 Applies to userspace execution only; libsu uses its own timeout.
     *
     * Always runs on [Dispatchers.IO].
     */
    suspend fun exec(
        command: String,
        asRoot: Boolean  = false,
        timeoutMs: Long  = DEFAULT_TIMEOUT_MS,
    ): ShellResult = withContext(Dispatchers.IO) {
        if (command.isBlank()) {
            return@withContext ShellResult(
                stdout   = emptyList(),
                stderr   = emptyList(),
                exitCode = 0,
                command  = command,
            )
        }

        checkBlocked(command)?.let { reason ->
            return@withContext ShellResult(
                stdout   = emptyList(),
                stderr   = listOf("JARVIS: command blocked — $reason"),
                exitCode = 126,
                command  = command,
                blocked  = true,
            )
        }

        val useRoot = asRoot && rootManager.isRooted

        return@withContext if (useRoot) {
            execRoot(command)
        } else {
            execUserspace(command, timeoutMs)
        }
    }

    // ── Root execution (libsu) ────────────────────────────────────────────

    private fun execRoot(command: String): ShellResult {
        val result = Shell.cmd(command).exec()
        val stdout = result.out.take(MAX_OUTPUT_LINES)
        // With FLAG_REDIRECT_STDERR, stderr comes through result.out;
        // result.err is populated only when the flag is NOT set.
        val stderr = result.err.take(MAX_OUTPUT_LINES)
        Log.d(TAG, "root exec [${result.code}]: ${command.take(80)}")
        return ShellResult(
            stdout   = stdout,
            stderr   = stderr,
            exitCode = result.code,
            command  = command,
        )
    }

    // ── Userspace execution (Runtime.exec) ───────────────────────────────

    private fun execUserspace(command: String, timeoutMs: Long): ShellResult {
        return try {
            val process = Runtime.getRuntime().exec(
                arrayOf("/system/bin/sh", "-c", command)
            )

            // Read stdout and stderr on separate threads to prevent deadlock
            val stdoutThread = CaptureThread(process.inputStream)
            val stderrThread = CaptureThread(process.errorStream)
            stdoutThread.start()
            stderrThread.start()

            val finished = process.waitFor(timeoutMs, java.util.concurrent.TimeUnit.MILLISECONDS)
            if (!finished) {
                process.destroyForcibly()
                return ShellResult(
                    stdout   = stdoutThread.lines().take(MAX_OUTPUT_LINES),
                    stderr   = listOf("JARVIS: command timed out after ${timeoutMs}ms"),
                    exitCode = -1,
                    command  = command,
                    timedOut = true,
                )
            }

            stdoutThread.join(500)
            stderrThread.join(500)

            val exitCode = process.exitValue()
            Log.d(TAG, "userspace exec [$exitCode]: ${command.take(80)}")
            ShellResult(
                stdout   = stdoutThread.lines().take(MAX_OUTPUT_LINES),
                stderr   = stderrThread.lines().take(MAX_OUTPUT_LINES),
                exitCode = exitCode,
                command  = command,
            )
        } catch (e: Exception) {
            Log.e(TAG, "exec failed: ${e.message}")
            ShellResult(
                stdout   = emptyList(),
                stderr   = listOf("JARVIS: exec error — ${e.message}"),
                exitCode = -1,
                command  = command,
            )
        }
    }

    // ── Stream capture helper ─────────────────────────────────────────────

    private class CaptureThread(
        private val stream: java.io.InputStream,
    ) : Thread("jarvis-capture") {
        private val buffer = mutableListOf<String>()

        override fun run() {
            try {
                stream.bufferedReader().forEachLine { line ->
                    if (buffer.size < MAX_OUTPUT_LINES) buffer.add(line)
                }
            } catch (_: Exception) { /* stream closed */ }
        }

        fun lines(): List<String> = buffer
    }

    // ── Denylist ──────────────────────────────────────────────────────────

    /**
     * Returns a non-null reason string if [command] matches a blocked pattern.
     * The AI tool dispatcher should never send these without explicit user
     * confirmation — but we double-check here as a safety net.
     */
    private fun checkBlocked(command: String): String? {
        val trimmed = command.trim()
        return BLOCKED_PATTERNS.firstOrNull { (pattern, _) ->
            pattern.containsMatchIn(trimmed)
        }?.second
    }

    private companion object {
        const val TAG             = "JarvisRootShell"
        const val DEFAULT_TIMEOUT_MS = 30_000L
        const val MAX_OUTPUT_LINES   = 2000

        /**
         * Regex patterns for commands that require explicit confirmation.
         * Matched against the trimmed command string before execution.
         *
         * These are not hard-blocked — the confirmation dialog in [JarvisToolDispatcher]
         * is the primary gate. This list catches any bypass attempts.
         */
        val BLOCKED_PATTERNS = listOf(
            Regex("""(?i)\brm\s+-rf\s+/\s*$""")     to "rm -rf / is permanently blocked",
            Regex("""(?i)\bmkfs\b""")                to "mkfs (format filesystem) requires explicit confirmation",
            Regex("""(?i)\bdd\b.+\bof=/dev/(sd|mmcblk|nvme)""") to "dd to block device requires confirmation",
            Regex("""(?i)\bwipe\b.+\b/data\b""")    to "wiping /data requires confirmation",
            Regex("""(?i)\bflash\b""")               to "flash commands require confirmation",
        )
    }
}

// ── Result data class ─────────────────────────────────────────────────────────

/**
 * Result of a shell command execution.
 *
 * @param stdout    Lines from standard output (capped at 2000).
 * @param stderr    Lines from standard error (may be empty if stderr was redirected to stdout).
 * @param exitCode  Process exit code. 0 = success, non-zero = failure. -1 = timeout/exception.
 * @param command   The original command string (for display in terminal history).
 * @param blocked   True if the command was blocked by the denylist.
 * @param timedOut  True if the command exceeded its timeout.
 */
data class ShellResult(
    val stdout:   List<String>,
    val stderr:   List<String>,
    val exitCode: Int,
    val command:  String,
    val blocked:  Boolean = false,
    val timedOut: Boolean = false,
) {
    /** True if the command exited with code 0 and was not blocked/timed-out. */
    val isSuccess: Boolean get() = exitCode == 0 && !blocked && !timedOut

    /** All output lines combined (stdout first, then stderr if non-empty). */
    val allOutput: List<String>
        get() = if (stderr.isEmpty()) stdout else stdout + stderr

    /** Combined output as a single string, lines joined with newline. */
    val outputText: String get() = allOutput.joinToString("\n")

    /**
     * Compact JSON-like summary for returning to the Claude tool_result.
     * Truncated to 8000 chars to stay within Claude's context limits.
     */
    fun toToolResultText(): String = buildString {
        append("exit_code: $exitCode\n")
        if (stdout.isNotEmpty()) {
            append("stdout:\n")
            append(stdout.joinToString("\n").take(7000))
            if (stdout.joinToString("\n").length > 7000) append("\n[truncated]")
        }
        if (stderr.isNotEmpty()) {
            append("\nstderr:\n")
            append(stderr.joinToString("\n").take(500))
        }
        if (timedOut) append("\n[command timed out]")
        if (blocked)  append("\n[command blocked by JARVIS safety policy]")
    }.take(8000)
}
