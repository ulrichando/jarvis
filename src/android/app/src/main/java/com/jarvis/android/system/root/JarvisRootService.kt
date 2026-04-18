package com.jarvis.android.system.root

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.IBinder
import android.os.Messenger
import android.os.Message
import android.os.Handler
import android.os.Looper
import android.util.Log
import com.topjohnwu.superuser.Shell
import com.topjohnwu.superuser.ipc.RootService
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Long-lived libsu [RootService] that runs in a root process.
 *
 * Unlike single-shot [Shell.cmd] calls, this service keeps a persistent root
 * process alive so that:
 *   - PTY sessions spawned via [PtyManager] stay alive when the app is backgrounded
 *   - [JarvisForegroundService] can call root operations without re-acquiring SU each time
 *   - The SELinux context is set once at bind time
 *
 * Architecture:
 *   App process → binds [JarvisRootService] via libsu → root process runs
 *   Communication: [Messenger] over Binder (no AIDL needed for our use case)
 *
 * Message protocol:
 *   MSG_EXEC_COMMAND   (1) → execute a shell command in the root process
 *   MSG_SET_SELINUX    (2) → set SELinux to permissive (if requested and possible)
 *   MSG_PING           (3) → liveness check; service replies MSG_PONG
 *   MSG_PONG           (4) → reply to MSG_PING
 *   MSG_RESULT         (5) → command result sent back to caller
 *
 * This service is declared in AndroidManifest.xml with android:exported="true"
 * because libsu binds it across the process boundary.
 *
 * Binding is managed by [RootServiceConnection] — callers do not bind directly.
 */
class JarvisRootService : RootService() {

    private val serviceScope = CoroutineScope(Dispatchers.IO + SupervisorJob())

    private val handler = object : Handler(Looper.getMainLooper()) {
        override fun handleMessage(msg: Message) {
            when (msg.what) {
                MSG_EXEC_COMMAND -> handleExecCommand(msg)
                MSG_SET_SELINUX  -> handleSetSeLinux(msg)
                MSG_PING -> {
                    val reply = Message.obtain(null, MSG_PONG)
                    try { msg.replyTo?.send(reply) } catch (_: Exception) {}
                }
                else -> super.handleMessage(msg)
            }
        }
    }

    private val messenger = Messenger(handler)

    override fun onBind(intent: Intent): IBinder {
        Log.i(TAG, "JarvisRootService bound in root process (uid=${android.os.Process.myUid()})")
        return messenger.binder
    }

    override fun onUnbind(intent: Intent): Boolean {
        Log.i(TAG, "JarvisRootService unbound")
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        serviceScope.cancel()
        Log.i(TAG, "JarvisRootService destroyed")
        super.onDestroy()
    }

    // ── Message handlers ──────────────────────────────────────────────────

    private fun handleExecCommand(msg: Message) {
        val command = msg.data?.getString(KEY_COMMAND) ?: return
        val replyTo = msg.replyTo ?: return
        val msgId   = msg.arg1

        serviceScope.launch {
            val result = Shell.cmd(command).exec()
            val stdout = result.out.take(MAX_LINES).joinToString("\n")
            val exitCode = result.code

            val reply = Message.obtain(null, MSG_RESULT).apply {
                arg1 = msgId
                arg2 = exitCode
                data = android.os.Bundle().apply {
                    putString(KEY_STDOUT, stdout.take(MAX_RESULT_CHARS))
                }
            }
            try { replyTo.send(reply) } catch (e: Exception) {
                Log.w(TAG, "Failed to send result: ${e.message}")
            }
        }
    }

    private fun handleSetSeLinux(msg: Message) {
        val mode    = msg.data?.getString(KEY_SELINUX_MODE) ?: "enforcing"
        val replyTo = msg.replyTo

        serviceScope.launch {
            val result = Shell.cmd("setenforce $mode").exec()
            val reply = Message.obtain(null, MSG_RESULT).apply {
                arg2 = result.code
                data = android.os.Bundle().apply {
                    putString(KEY_STDOUT, result.out.joinToString("\n"))
                }
            }
            try { replyTo?.send(reply) } catch (_: Exception) {}
        }
    }

    companion object {
        const val TAG = "JarvisRootService"

        // Message types
        const val MSG_EXEC_COMMAND = 1
        const val MSG_SET_SELINUX  = 2
        const val MSG_PING         = 3
        const val MSG_PONG         = 4
        const val MSG_RESULT       = 5

        // Bundle keys
        const val KEY_COMMAND      = "cmd"
        const val KEY_STDOUT       = "out"
        const val KEY_SELINUX_MODE = "selinux_mode"

        private const val MAX_LINES        = 2000
        private const val MAX_RESULT_CHARS = 65536
    }
}

// ── Connection manager ────────────────────────────────────────────────────────

/**
 * Manages binding to [JarvisRootService] via libsu's [RootService.bind].
 *
 * Injected into [JarvisForegroundService] and [RootModule] to keep the
 * root process alive for the app's lifetime.
 *
 * Usage:
 *   rootServiceConnection.bind(context)
 *   // service is now bound; PTY sessions and root commands work
 *   rootServiceConnection.unbind(context)
 */
@Singleton
class RootServiceConnection @Inject constructor(
    @ApplicationContext private val context: Context,
    private val rootManager: RootManager,
) {
    private var messenger: Messenger? = null
    private var isBound = false

    private val connection = object : android.content.ServiceConnection {
        override fun onServiceConnected(name: ComponentName, service: IBinder) {
            messenger = Messenger(service)
            isBound   = true
            Log.i(TAG, "RootService connected")
        }

        override fun onServiceDisconnected(name: ComponentName) {
            messenger = null
            isBound   = false
            Log.w(TAG, "RootService disconnected")
        }
    }

    /**
     * Bind to [JarvisRootService] if root is available.
     * Safe to call multiple times — subsequent calls are no-ops if already bound.
     */
    fun bind() {
        if (isBound || !rootManager.isRooted) return
        val intent = Intent(context, JarvisRootService::class.java)
        RootService.bind(intent, connection)
        Log.i(TAG, "Binding to JarvisRootService…")
    }

    /** Unbind from [JarvisRootService]. Call from [JarvisForegroundService.onDestroy]. */
    fun unbind() {
        if (!isBound) return
        val intent = Intent(context, JarvisRootService::class.java)
        RootService.unbind(connection)
        isBound   = false
        messenger = null
        Log.i(TAG, "Unbound from JarvisRootService")
    }

    /** Send a fire-and-forget command through the bound root service. */
    fun sendMessage(msg: Message) {
        if (!isBound) {
            Log.w(TAG, "sendMessage: not bound")
            return
        }
        try {
            messenger?.send(msg)
        } catch (e: Exception) {
            Log.e(TAG, "sendMessage failed: ${e.message}")
        }
    }

    val isConnected: Boolean get() = isBound

    private companion object {
        const val TAG = "RootServiceConnection"
    }
}
