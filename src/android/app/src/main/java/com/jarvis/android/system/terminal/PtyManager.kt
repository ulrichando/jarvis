package com.jarvis.android.system.terminal

import android.util.Log
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Kotlin JNI bridge to the native PTY implementation in [pty_bridge.cpp].
 *
 * Responsibilities:
 *   - Load `libjarvis_pty.so` once at class init
 *   - Declare all `external` methods matching the JNI function signatures in C++
 *   - Provide the [READ_TIMEOUT_MS] constant used by the reader loop in
 *     [TerminalSessionManager]
 *
 * This class intentionally has NO state — it is a thin JNI wrapper.
 * Session lifecycle (fd tracking, reader coroutines, VtParser) lives in
 * [TerminalSessionManager].
 *
 * Thread safety: All methods are thread-safe — the native layer uses a mutex
 * for the pid map and individual fd operations are atomic at the OS level.
 * [nativeReadFromPty] blocks the calling thread for up to [READ_TIMEOUT_MS];
 * always call it from a dedicated IO thread/coroutine.
 */
@Singleton
class PtyManager @Inject constructor() {

    // ── JNI declarations ──────────────────────────────────────────────────

    /**
     * Allocates a PTY pair, forks [shellPath] (or auto-resolves if blank), and
     * returns the master file descriptor. Returns -1 on failure.
     *
     * @param rows      Initial terminal height in character rows.
     * @param cols      Initial terminal width in character columns.
     * @param shellPath Full path to the shell binary. Pass an empty string to
     *                  auto-resolve (zsh → bash → sh order).
     */
    external fun nativeCreatePty(rows: Int, cols: Int, shellPath: String): Int

    /**
     * Writes [length] bytes from [data] to the PTY master fd.
     * Handles partial writes internally. Safe to call from any thread.
     */
    external fun nativeWriteToPty(fd: Int, data: ByteArray, length: Int)

    /**
     * Reads available data from the PTY master, blocking up to [timeoutMs].
     *
     * Returns:
     *   null        — timeout; no data within [timeoutMs] (call again)
     *   empty array — EOF; the child shell has exited
     *   data array  — bytes of shell output to feed into [VtParser]
     *
     * Call from a dedicated coroutine on [Dispatchers.IO].
     */
    external fun nativeReadFromPty(fd: Int, timeoutMs: Int): ByteArray?

    /**
     * Sends TIOCSWINSZ to the PTY to update the terminal window size.
     * Call whenever the [TerminalView] Composable reports a new size.
     */
    external fun nativeResizePty(fd: Int, rows: Int, cols: Int)

    /**
     * Sends SIGHUP to the child shell, waits 150 ms, then SIGKILLs it and
     * closes the master fd. Safe to call even if already closed.
     */
    external fun nativeClosePty(fd: Int)

    /**
     * Returns the PID of the shell child for this [fd], or -1 if unknown.
     * Used by the System Dashboard to show which PID owns a terminal session.
     */
    external fun nativeGetChildPid(fd: Int): Int

    /**
     * Force kernel-level ECHO + ICANON on the given master PTY fd. Needed
     * on some Android builds where the shell or its editline clears these
     * silently after exec, producing the "I type but nothing appears" bug.
     * Safe to call repeatedly.
     */
    external fun nativeForceEcho(fd: Int)

    companion object {
        /** Timeout passed to [nativeReadFromPty] — keeps the reader loop responsive. */
        const val READ_TIMEOUT_MS = 50

        /** Maximum simultaneous PTY sessions. */
        const val MAX_SESSIONS = 8

        private const val TAG = "JarvisPtyManager"

        init {
            try {
                System.loadLibrary("jarvis_pty")
                Log.i(TAG, "libjarvis_pty.so loaded")
            } catch (e: UnsatisfiedLinkError) {
                Log.e(TAG, "Failed to load libjarvis_pty.so — terminal features unavailable: ${e.message}")
            }
        }
    }
}
