package com.jarvis.android.system.adb

import android.content.Context
import android.util.Log
import dadb.AdbKeyPair
import dadb.Dadb
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import java.io.File
import javax.inject.Inject
import javax.inject.Singleton

/**
 * On-device ADB shell client.
 *
 * Connects to the local `adbd` via TCP (default localhost:5555) using the standard
 * ADB protocol, giving JARVIS the same capabilities as `adb shell` from a PC —
 * without needing root.
 *
 * ## What `adb shell` can do (and root cannot do on a locked device)
 * Running as the `shell` user (uid 2000) grants access to:
 * - `pm grant` / `pm revoke`              → dangerous runtime permissions
 * - `appops set`                          → special app-ops permissions
 * - `settings put secure/global/system`  → secure settings (including accessibility)
 * - `cmd notification allow_listener`    → notification listener
 * - `dpm set-active-admin`               → device admin
 * - `dumpsys deviceidle whitelist`       → battery optimisation exemption
 *
 * ## One-time setup (no PC needed after this)
 * 1. On device: Settings → Developer Options → Wireless Debugging → enable
 * 2. Note the port shown (usually 5555).
 * 3. Tap "Grant ADB Access" in JARVIS — the system shows a single "Allow debugging?" dialog.
 * 4. Done. JARVIS re-connects automatically on every restart.
 *
 * ## Authentication
 * An RSA key pair is generated once and stored in the app's private files directory
 * (`adb_key`). The device remembers the key — subsequent connections are silent.
 *
 * ## Fallback
 * If [connect] fails (ADB not enabled / wrong port), [state] stays [AdbState.Disconnected]
 * and all callers gracefully degrade to the wizard-based manual flow.
 */
@Singleton
class AdbManager @Inject constructor(
    @ApplicationContext private val context: Context,
) {

    private val _state = MutableStateFlow<AdbState>(AdbState.Disconnected)
    val state: StateFlow<AdbState> = _state.asStateFlow()

    val isConnected: Boolean get() = _state.value is AdbState.Connected

    private var dadb: Dadb? = null

    // ── Connection ────────────────────────────────────────────────────────

    /**
     * Attempt to connect to the ADB daemon.
     *
     * @param host  Usually "localhost" (connects to the device's own adbd).
     * @param port  Default 5555. Wireless Debugging may use a different port —
     *              the user can override from the setup screen.
     */
    suspend fun connect(
        host: String = DEFAULT_HOST,
        port: Int    = DEFAULT_PORT,
    ): Boolean = withContext(Dispatchers.IO) {
        _state.value = AdbState.Connecting
        try {
            val kp   = loadOrCreateKeyPair()
            val conn = Dadb.create(host, port, kp)
            // Quick smoke-test — echo to verify the shell works
            val test = conn.shell("echo JARVIS_ADB_OK")
            if (!test.allOutput.contains("JARVIS_ADB_OK")) {
                conn.close()
                _state.value = AdbState.Disconnected
                Log.w(TAG, "Shell smoke-test failed: ${test.allOutput}")
                return@withContext false
            }
            dadb         = conn
            _state.value = AdbState.Connected(host, port)
            Log.i(TAG, "ADB connected to $host:$port")
            true
        } catch (e: Exception) {
            _state.value = AdbState.Error(e.message ?: "Connection failed")
            Log.w(TAG, "ADB connect failed ($host:$port): ${e.message}")
            false
        }
    }

    /** Close the current ADB connection. */
    fun disconnect() {
        dadb?.close()
        dadb         = null
        _state.value = AdbState.Disconnected
        Log.i(TAG, "ADB disconnected")
    }

    // ── Command execution ─────────────────────────────────────────────────

    /**
     * Run a shell command and return the combined output.
     *
     * @throws IllegalStateException if not connected.
     */
    suspend fun exec(command: String): AdbShellResult = withContext(Dispatchers.IO) {
        val conn = dadb ?: return@withContext AdbShellResult(
            output   = "",
            exitCode = -1,
            error    = "ADB not connected",
        )
        return@withContext try {
            val result = conn.shell(command)
            Log.d(TAG, "adb shell [${result.exitCode}]: ${command.take(80)}")
            AdbShellResult(
                output   = result.allOutput.trim(),
                exitCode = result.exitCode,
                error    = "",
            )
        } catch (e: Exception) {
            // Connection was lost — reset state so the UI prompts reconnect
            Log.w(TAG, "adb exec error (${command.take(40)}): ${e.message}")
            dadb         = null
            _state.value = AdbState.Disconnected
            AdbShellResult(
                output   = "",
                exitCode = -1,
                error    = e.message ?: "Execution failed",
            )
        }
    }

    // ── RSA key management ────────────────────────────────────────────────

    /**
     * Load the persisted ADB key pair from the app's private storage, or generate
     * a new one if none exists.  The public key is sent to adbd on first connect;
     * the user approves it once via the system dialog, after which adbd remembers it.
     *
     * `AdbKeyPair.generate(privateFile, publicFile)` writes both files and returns Unit.
     * `AdbKeyPair.read(privateFile, publicFile)` reads them back as an [AdbKeyPair].
     */
    private fun loadOrCreateKeyPair(): AdbKeyPair {
        val privFile = File(context.filesDir, KEY_FILE_PRIV)
        val pubFile  = File(context.filesDir, KEY_FILE_PUB)
        return if (privFile.exists() && pubFile.exists()) {
            try {
                AdbKeyPair.read(privFile, pubFile)
            } catch (e: Exception) {
                Log.w(TAG, "Key files corrupt — regenerating: ${e.message}")
                privFile.delete()
                pubFile.delete()
                generateAndRead(privFile, pubFile)
            }
        } else {
            Log.i(TAG, "Generating new ADB key pair")
            generateAndRead(privFile, pubFile)
        }
    }

    private fun generateAndRead(privFile: File, pubFile: File): AdbKeyPair {
        AdbKeyPair.generate(privFile, pubFile)
        return AdbKeyPair.read(privFile, pubFile)
    }

    private companion object {
        const val TAG           = "JarvisAdbManager"
        const val KEY_FILE_PRIV = "jarvis_adb_key"
        const val KEY_FILE_PUB  = "jarvis_adb_key.pub"
        const val DEFAULT_HOST  = "localhost"
        const val DEFAULT_PORT  = 5555
    }
}

// ── State ─────────────────────────────────────────────────────────────────────

sealed interface AdbState {
    data object Disconnected                           : AdbState
    data object Connecting                             : AdbState
    data class  Connected(val host: String, val port: Int) : AdbState
    data class  Error(val message: String)             : AdbState
}

// ── Result ────────────────────────────────────────────────────────────────────

data class AdbShellResult(
    val output:   String,
    val exitCode: Int,
    val error:    String = "",
) {
    val isSuccess: Boolean get() = exitCode == 0
}
