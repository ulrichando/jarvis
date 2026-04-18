package com.jarvis.android.system.root

import android.util.Log
import com.topjohnwu.superuser.Shell
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Singleton manager for the libsu root shell lifecycle.
 *
 * Responsibilities:
 *   - Configure libsu [Shell.Builder] once at app startup (called from [JarvisApplication])
 *   - Request and track root grant status
 *   - Expose [rootState] as a [StateFlow] for reactive UI (Permission Matrix, status bar)
 *   - Detect whether the device runs Magisk or KernelSU
 *
 * All root operations go through [RootShell], not this class.
 * This class only manages the lifecycle and state.
 *
 * Non-root features must work without root — if [rootState] is [RootState.Denied]
 * or [RootState.Unavailable], those features show a graceful empty state.
 */
@Singleton
class RootManager @Inject constructor() {

    private val _rootState = MutableStateFlow<RootState>(RootState.Unknown)
    val rootState: StateFlow<RootState> = _rootState.asStateFlow()

    val isRooted: Boolean
        get() = _rootState.value is RootState.Granted

    /**
     * Configure libsu. Must be called before any [Shell] usage.
     * Invoked once from [JarvisApplication.onCreate] via an Android Startup initializer.
     *
     * Flags:
     *   FLAG_REDIRECT_STDERR — merge stderr into stdout so stdout captures everything
     *   FLAG_NON_ROOT_SHELL  — allows a non-root fallback shell (graceful degradation)
     *   setTimeout(10)       — 10s for the superuser dialog; if no response, assume denied
     */
    fun configure() {
        Shell.setDefaultBuilder(
            Shell.Builder.create()
                .setFlags(Shell.FLAG_REDIRECT_STDERR or Shell.FLAG_NON_ROOT_SHELL)
                .setTimeout(10)
        )
    }

    /**
     * Asynchronously request root access and update [rootState].
     *
     * Called on first app launch (or after denial) from [PermissionMatrixScreen].
     * Runs on [Dispatchers.IO] — [Shell.getShell] blocks until the SU dialog resolves.
     */
    suspend fun requestRoot(): RootState = withContext(Dispatchers.IO) {
        _rootState.value = RootState.Requesting
        try {
            val shell = Shell.getShell()
            val granted = shell.isRoot
            val state = if (granted) {
                val info = detectRootProvider()
                Log.i(TAG, "Root granted via $info")
                RootState.Granted(provider = info)
            } else {
                Log.w(TAG, "Root denied or non-root shell")
                RootState.Denied
            }
            _rootState.value = state
            state
        } catch (e: Exception) {
            Log.e(TAG, "Root request failed: ${e.message}")
            val state = RootState.Unavailable(reason = e.message ?: "Unknown error")
            _rootState.value = state
            state
        }
    }

    /**
     * Refresh root state without prompting the user.
     * Uses [Shell.isAppGrantedRoot] which returns the cached grant status.
     */
    suspend fun refreshState() = withContext(Dispatchers.IO) {
        val granted = Shell.isAppGrantedRoot()
        _rootState.value = when {
            granted == null -> RootState.Unknown
            granted         -> RootState.Granted(provider = detectRootProvider())
            else            -> RootState.Denied
        }
    }

    /**
     * Probes the device to determine which root provider is active.
     * Checks Magisk first (most common), then KernelSU.
     */
    private fun detectRootProvider(): RootProvider {
        // Magisk — daemon socket or app package
        val magiskResult = Shell.cmd("magisk --version").exec()
        if (magiskResult.isSuccess) {
            val version = magiskResult.out.firstOrNull()?.trim() ?: "unknown"
            return RootProvider.Magisk(version = version)
        }
        // KernelSU — ksud binary
        val ksuResult = Shell.cmd("ksud --version").exec()
        if (ksuResult.isSuccess) {
            val version = ksuResult.out.firstOrNull()?.trim() ?: "unknown"
            return RootProvider.KernelSU(version = version)
        }
        // APatch or other su binary
        val suResult = Shell.cmd("su --version 2>/dev/null || su -v 2>/dev/null").exec()
        if (suResult.isSuccess) {
            return RootProvider.Other(name = suResult.out.firstOrNull()?.trim() ?: "su")
        }
        return RootProvider.Unknown
    }

    /**
     * Returns a human-readable label for the current root provider.
     * Displayed in [SystemDashboardScreen] device info card.
     */
    fun rootProviderLabel(): String = when (val state = _rootState.value) {
        is RootState.Granted -> when (val p = state.provider) {
            is RootProvider.Magisk   -> "Magisk ${p.version}"
            is RootProvider.KernelSU -> "KernelSU ${p.version}"
            is RootProvider.Other    -> p.name
            RootProvider.Unknown     -> "Rooted (unknown)"
        }
        is RootState.Denied      -> "Not rooted"
        is RootState.Unavailable -> "Not available"
        RootState.Unknown,
        RootState.Requesting     -> "Checking…"
    }

    private companion object {
        const val TAG = "JarvisRootManager"
    }
}

// ── State sealed hierarchy ────────────────────────────────────────────────────

/** Current root access state, exposed via [RootManager.rootState]. */
sealed interface RootState {
    /** Initial state — not yet checked. */
    data object Unknown : RootState

    /** [RootManager.requestRoot] is in progress. */
    data object Requesting : RootState

    /** Root is available and the SU dialog was accepted. */
    data class Granted(val provider: RootProvider) : RootState

    /** The SU dialog was denied by the user, or the device lacks su. */
    data object Denied : RootState

    /** Root check failed with an exception (e.g. libsu crash). */
    data class Unavailable(val reason: String) : RootState
}

// ── Root provider sealed hierarchy ───────────────────────────────────────────

/** Which root implementation is active on this device. */
sealed interface RootProvider {
    data class Magisk(val version: String)   : RootProvider
    data class KernelSU(val version: String) : RootProvider
    data class Other(val name: String)       : RootProvider
    data object Unknown                      : RootProvider
}
