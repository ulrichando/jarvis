/**
 * pty_bridge.cpp — JNI bridge between Kotlin PtyManager and POSIX PTY API.
 *
 * Allocates a master/slave PTY pair via openpty(), forks a child shell,
 * and exposes read/write/resize/close operations to Kotlin over JNI.
 *
 * Thread safety:
 *   nativeCreatePty  — call from any thread once per session
 *   nativeWriteToPty — call from UI/coroutine thread
 *   nativeReadFromPty — call from a dedicated reader coroutine (blocking select)
 *   nativeResizePty  — call from UI thread on layout change
 *   nativeClosePty   — call once; safe to call from any thread
 *
 * All Kotlin-facing class: com.jarvis.android.system.terminal.PtyManager
 */

#include <jni.h>
#include <pty.h>          // openpty() — Bionic API 26+, needs _XOPEN_SOURCE=700
#include <unistd.h>
#include <fcntl.h>
#include <termios.h>
#include <sys/ioctl.h>
#include <sys/select.h>
#include <sys/wait.h>
#include <signal.h>
#include <string.h>
#include <errno.h>
#include <stdlib.h>
#include <android/log.h>

#include <mutex>
#include <string>
#include <unordered_map>

// ── Logging ──────────────────────────────────────────────────────────────────
#define LOG_TAG "JarvisPTY"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO,  LOG_TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)

// ── Internal state ────────────────────────────────────────────────────────────

struct PtySession {
    pid_t child_pid;
};

static std::unordered_map<int /*master_fd*/, PtySession> s_sessions;
static std::mutex s_sessions_mutex;

// ── Helpers ───────────────────────────────────────────────────────────────────

static void set_cloexec(int fd) {
    int flags = fcntl(fd, F_GETFD, 0);
    if (flags >= 0) fcntl(fd, F_SETFD, flags | FD_CLOEXEC);
}

// Resolve the best available shell on the device.
// Preference order: zsh (Termux/custom) > bash > sh
static const char* resolve_shell() {
    // zsh — Termux install (most common on rooted/developer devices)
    if (access("/data/data/com.termux/files/usr/bin/zsh", X_OK) == 0)
        return "/data/data/com.termux/files/usr/bin/zsh";
    if (access("/data/user/0/com.termux/files/usr/bin/zsh", X_OK) == 0)
        return "/data/user/0/com.termux/files/usr/bin/zsh";
    // zsh — system / manual install
    if (access("/system/bin/zsh", X_OK) == 0)  return "/system/bin/zsh";
    if (access("/system/xbin/zsh", X_OK) == 0) return "/system/xbin/zsh";
    if (access("/data/local/tmp/zsh", X_OK) == 0) return "/data/local/tmp/zsh";
    // bash — busybox / termux
    if (access("/data/data/com.termux/files/usr/bin/bash", X_OK) == 0)
        return "/data/data/com.termux/files/usr/bin/bash";
    if (access("/system/bin/bash", X_OK) == 0)  return "/system/bin/bash";
    if (access("/system/xbin/bash", X_OK) == 0) return "/system/xbin/bash";
    // POSIX sh — always present on Android
    return "/system/bin/sh";
}

// ── JNI Exports ───────────────────────────────────────────────────────────────
extern "C" {

/**
 * int nativeCreatePty(rows: Int, cols: Int, shellPath: String): Int
 *
 * Allocates a PTY pair, forks the given shell (or auto-resolves if shellPath
 * is empty) as child, and returns the master file descriptor to Kotlin.
 * Returns -1 on error.
 */
JNIEXPORT jint JNICALL
Java_com_jarvis_android_system_terminal_PtyManager_nativeCreatePty(
        JNIEnv* env, jobject /* thiz */, jint rows, jint cols, jstring shell_path_j) {

    // Resolve shell: use the caller's preference if provided, else auto-detect
    std::string shell_str;
    if (shell_path_j != nullptr) {
        const char* sp = env->GetStringUTFChars(shell_path_j, nullptr);
        if (sp && *sp != '\0') shell_str = sp;
        if (sp) env->ReleaseStringUTFChars(shell_path_j, sp);
    }
    const char* shell = shell_str.empty() ? resolve_shell() : shell_str.c_str();

    int master_fd = -1;
    int slave_fd  = -1;

    struct winsize ws{};
    ws.ws_row    = static_cast<unsigned short>(rows > 0 ? rows : 24);
    ws.ws_col    = static_cast<unsigned short>(cols > 0 ? cols : 80);
    ws.ws_xpixel = 0;
    ws.ws_ypixel = 0;

    if (openpty(&master_fd, &slave_fd, nullptr, nullptr, &ws) != 0) {
        LOGE("openpty() failed: %s (errno=%d)", strerror(errno), errno);
        return -1;
    }

    set_cloexec(master_fd);
    // slave_fd does NOT get CLOEXEC — the child needs to inherit it via dup2

    LOGI("Forking shell: %s (pty master=%d slave=%d rows=%d cols=%d)",
         shell, master_fd, slave_fd, rows, cols);

    // Build SHELL= env var using the actual shell path (must outlive fork)
    std::string shell_env_str = std::string("SHELL=") + shell;

    pid_t child_pid = fork();
    if (child_pid < 0) {
        LOGE("fork() failed: %s", strerror(errno));
        close(master_fd);
        close(slave_fd);
        return -1;
    }

    if (child_pid == 0) {
        // ── CHILD ──────────────────────────────────────────────────────────
        close(master_fd);

        // New session — child becomes process group leader with no ctty
        if (setsid() < 0) { _exit(1); }

        // Assign slave as controlling terminal (TIOCSCTTY)
        if (ioctl(slave_fd, TIOCSCTTY, 0) < 0) { _exit(1); }

        // Redirect stdio to the slave PTY
        dup2(slave_fd, STDIN_FILENO);
        dup2(slave_fd, STDOUT_FILENO);
        dup2(slave_fd, STDERR_FILENO);
        if (slave_fd > STDERR_FILENO) close(slave_fd);

        // Environment — standard paths + common root locations + Termux
        // clang-format off
        char* child_env[] = {
            const_cast<char*>("TERM=xterm-256color"),
            const_cast<char*>("COLORTERM=truecolor"),
            const_cast<char*>("LANG=en_US.UTF-8"),
            const_cast<char*>("LC_ALL=en_US.UTF-8"),
            const_cast<char*>("PATH=/system/bin:/system/xbin:/sbin"
                              ":/su/bin:/su/xbin"
                              ":/magisk/usr/bin"
                              ":/data/local/tmp"
                              ":/data/data/com.termux/files/usr/bin"
                              ":/data/data/com.termux/files/usr/sbin"),
            const_cast<char*>("HOME=/data/local/tmp"),
            const_cast<char*>("USER=shell"),
            const_cast<char*>("LOGNAME=shell"),
            const_cast<char*>(shell_env_str.c_str()),
            nullptr
        };
        // clang-format on

        execle(shell, shell, nullptr, child_env);
        // execle only returns on failure
        LOGE("execle(%s) failed: %s", shell, strerror(errno));
        _exit(127);
    }

    // ── PARENT ─────────────────────────────────────────────────────────────
    close(slave_fd);  // parent never reads from the slave side

    {
        std::lock_guard<std::mutex> lock(s_sessions_mutex);
        s_sessions[master_fd] = PtySession{ child_pid };
    }

    LOGI("PTY session created: master_fd=%d child_pid=%d", master_fd, child_pid);
    return master_fd;
}

/**
 * void nativeWriteToPty(fd: Int, data: ByteArray, length: Int)
 *
 * Writes keystroke / paste data to the PTY master. Handles partial writes.
 */
JNIEXPORT void JNICALL
Java_com_jarvis_android_system_terminal_PtyManager_nativeWriteToPty(
        JNIEnv* env, jobject /* thiz */, jint fd, jbyteArray data, jint length) {

    if (fd < 0 || data == nullptr || length <= 0) return;

    jbyte* buf = env->GetByteArrayElements(data, nullptr);
    if (!buf) return;

    const char* ptr      = reinterpret_cast<const char*>(buf);
    ssize_t     total    = static_cast<ssize_t>(length);
    ssize_t     written  = 0;

    while (written < total) {
        ssize_t n = write(fd, ptr + written, static_cast<size_t>(total - written));
        if (n < 0) {
            if (errno == EINTR) continue;
            LOGE("write() to PTY fd=%d failed: %s", fd, strerror(errno));
            break;
        }
        written += n;
    }

    env->ReleaseByteArrayElements(data, buf, JNI_ABORT);
}

/**
 * ByteArray? nativeReadFromPty(fd: Int, timeoutMs: Int): ByteArray?
 *
 * Blocks for up to timeoutMs waiting for data. Returns:
 *   null        — timeout, no data (loop and retry)
 *   empty array — EOF / PTY hangup (child exited)
 *   data array  — bytes read from the shell output
 *
 * Call from a dedicated coroutine on Dispatchers.IO:
 *   while (active) {
 *     val bytes = nativeReadFromPty(fd, 50) ?: continue
 *     if (bytes.isEmpty()) break  // shell exited
 *     vtParser.feed(bytes)
 *   }
 */
JNIEXPORT jbyteArray JNICALL
Java_com_jarvis_android_system_terminal_PtyManager_nativeReadFromPty(
        JNIEnv* env, jobject /* thiz */, jint fd, jint timeout_ms) {

    if (fd < 0) return nullptr;

    fd_set rfds;
    FD_ZERO(&rfds);
    FD_SET(static_cast<unsigned>(fd), &rfds);

    struct timeval tv{};
    tv.tv_sec  = timeout_ms / 1000;
    tv.tv_usec = (timeout_ms % 1000) * 1000L;

    int sel = select(fd + 1, &rfds, nullptr, nullptr, &tv);
    if (sel < 0) {
        if (errno == EINTR) return nullptr;  // interrupted, retry
        LOGE("select() on PTY fd=%d failed: %s", fd, strerror(errno));
        return nullptr;
    }
    if (sel == 0) return nullptr;  // timeout — no data

    // Up to 8 KB per read — balances throughput vs JNI object size
    constexpr int READ_BUF = 8192;
    char buf[READ_BUF];
    ssize_t n = read(fd, buf, READ_BUF);

    if (n <= 0) {
        // n == 0: EOF; n < 0: EIO (child closed slave) — both mean "done"
        LOGI("PTY fd=%d EOF/EIO — shell exited", fd);
        return env->NewByteArray(0);
    }

    jbyteArray result = env->NewByteArray(static_cast<jsize>(n));
    env->SetByteArrayRegion(result, 0, static_cast<jsize>(n),
                            reinterpret_cast<const jbyte*>(buf));
    return result;
}

/**
 * void nativeResizePty(fd: Int, rows: Int, cols: Int)
 *
 * Sends TIOCSWINSZ to update the PTY window size. Call whenever the
 * terminal Composable is resized (onSizeChanged).
 */
JNIEXPORT void JNICALL
Java_com_jarvis_android_system_terminal_PtyManager_nativeResizePty(
        JNIEnv* /* env */, jobject /* thiz */, jint fd, jint rows, jint cols) {

    if (fd < 0 || rows <= 0 || cols <= 0) return;

    struct winsize ws{};
    ws.ws_row    = static_cast<unsigned short>(rows);
    ws.ws_col    = static_cast<unsigned short>(cols);
    ws.ws_xpixel = 0;
    ws.ws_ypixel = 0;

    if (ioctl(fd, TIOCSWINSZ, &ws) < 0) {
        LOGE("TIOCSWINSZ on fd=%d failed: %s", fd, strerror(errno));
    }
}

/**
 * void nativeClosePty(fd: Int)
 *
 * Sends SIGHUP to the child shell (graceful), waits briefly, then SIGKILLs
 * it and closes the master fd.
 */
JNIEXPORT void JNICALL
Java_com_jarvis_android_system_terminal_PtyManager_nativeClosePty(
        JNIEnv* /* env */, jobject /* thiz */, jint fd) {

    if (fd < 0) return;

    pid_t child_pid = -1;
    {
        std::lock_guard<std::mutex> lock(s_sessions_mutex);
        auto it = s_sessions.find(fd);
        if (it != s_sessions.end()) {
            child_pid = it->second.child_pid;
            s_sessions.erase(it);
        }
    }

    if (child_pid > 0) {
        kill(child_pid, SIGHUP);
        // Give the shell 150 ms to exit cleanly
        usleep(150000);
        // Reap without blocking — if it's still alive, SIGKILL it
        if (waitpid(child_pid, nullptr, WNOHANG) == 0) {
            kill(child_pid, SIGKILL);
            waitpid(child_pid, nullptr, 0);
        }
    }

    close(fd);
    LOGI("PTY closed: fd=%d child_pid=%d", fd, child_pid);
}

/**
 * int nativeGetChildPid(fd: Int): Int
 *
 * Returns the PID of the shell child for the given master fd.
 * Used by the system dashboard to show which PID owns each terminal session.
 */
JNIEXPORT jint JNICALL
Java_com_jarvis_android_system_terminal_PtyManager_nativeGetChildPid(
        JNIEnv* /* env */, jobject /* thiz */, jint fd) {

    std::lock_guard<std::mutex> lock(s_sessions_mutex);
    auto it = s_sessions.find(fd);
    return (it != s_sessions.end())
        ? static_cast<jint>(it->second.child_pid)
        : -1;
}

} // extern "C"
