package com.jarvis.android.presentation.permissions

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.android.system.adb.AdbManager
import com.jarvis.android.system.adb.AdbState
import com.jarvis.android.system.permissions.PermissionEntry
import com.jarvis.android.system.permissions.PermissionManager
import com.jarvis.android.system.permissions.PermissionStatus
import com.jarvis.android.system.permissions.PermissionTier
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * Thin wrapper over [PermissionManager].
 *
 * The permission list is owned and live-updated by [PermissionManager]; the
 * ViewModel simply exposes it and dispatches grant/refresh actions.
 * No additional state copy needed — the StateFlow from PermissionManager
 * is already UI-ready.
 */
@HiltViewModel
class PermissionViewModel @Inject constructor(
    @ApplicationContext private val context: Context,
    private val permissionManager: PermissionManager,
    private val adbManager: AdbManager,
) : ViewModel() {

    val permissions: StateFlow<List<PermissionEntry>> = permissionManager.permissions

    /**
     * True while the "Grant All" wizard is cycling through Settings-based special permissions.
     * The screen observes this to auto-open the next special permission on every ON_RESUME.
     */
    private val _autoGrantMode = MutableStateFlow(false)
    val autoGrantMode: StateFlow<Boolean> = _autoGrantMode.asStateFlow()

    /** True while [grantAllViaRoot] is running — drives the loading indicator in the UI. */
    private val _rootGranting = MutableStateFlow(false)
    val rootGranting: StateFlow<Boolean> = _rootGranting.asStateFlow()

    /** Whether root is available — used by the screen to pick the grant strategy. */
    val isRooted: Boolean get() = permissionManager.isRooted

    /** Live ADB connection state — drives the "Connect ADB" button in the UI. */
    val adbState: StateFlow<AdbState> = adbManager.state

    /** True when ADB is connected and ready to run shell commands. */
    val isAdbConnected: Boolean get() = permissionManager.isAdbConnected

    /** Re-check all statuses — call from the screen's [androidx.compose.runtime.DisposableEffect] on resume. */
    fun refresh() = permissionManager.refresh()

    /**
     * Dispatch a grant action for [entry] using the correct mechanism for its tier.
     *
     * For DANGEROUS permissions, the caller should use [getMissingDangerousManifests] and
     * pass the result to `rememberLauncherForActivityResult(RequestMultiplePermissions())` —
     * that is the only way to receive the grant result in a Composable. This method handles
     * SPECIAL and ROOT tiers only; DANGEROUS is kept as a fallback that opens App Settings.
     */
    fun grant(entry: PermissionEntry) {
        when (entry.tier) {
            PermissionTier.DANGEROUS -> permissionManager.openAppSettings(context)
            PermissionTier.SPECIAL   -> permissionManager.openSpecialSettings(context, entry)
            PermissionTier.ROOT      -> { /* user must grant in Magisk/KernelSU */ }
        }
    }

    /**
     * Returns deduplicated manifest strings for all missing DANGEROUS permissions in [entries],
     * **excluding** ACCESS_BACKGROUND_LOCATION which must be requested in a separate launcher
     * (Android 11+ auto-denies it if batched with other permissions before foreground location
     * is granted). Manifest strings are deduped because Bluetooth maps to the same string on
     * pre-API-31 and nearby-wifi maps to ACCESS_FINE_LOCATION on pre-API-33.
     */
    fun getMissingDangerousManifests(entries: List<PermissionEntry>): List<String> =
        entries
            .filter { it.status != PermissionStatus.GRANTED
                    && it.tier == PermissionTier.DANGEROUS
                    && it.id != "bg_location" }
            .mapNotNull { it.manifestName }
            .filter { it.isNotBlank() }
            .distinct()

    /**
     * Returns the ACCESS_BACKGROUND_LOCATION manifest string only when:
     * - bg_location is not yet granted, AND
     * - at least one of fine_location / coarse_location is already granted.
     *
     * Returns null otherwise — requesting background location without foreground location
     * already granted results in an automatic denial on Android 11+.
     */
    fun getBackgroundLocationManifest(entries: List<PermissionEntry>): String? {
        val bgEntry = entries.find { it.id == "bg_location" } ?: return null
        if (bgEntry.status == PermissionStatus.GRANTED) return null
        val manifest = bgEntry.manifestName?.takeIf { it.isNotBlank() } ?: return null
        val fgGranted = entries.any {
            (it.id == "fine_location" || it.id == "coarse_location")
                    && it.status == PermissionStatus.GRANTED
        }
        return if (fgGranted) manifest else null
    }

    /**
     * Grant ALL missing permissions silently using root shell commands.
     * Requires [isRooted] to be true.  The screen observes [rootGranting] to show
     * a loading state while the commands run.
     */
    fun grantAllViaRoot() {
        viewModelScope.launch {
            _rootGranting.value = true
            permissionManager.rootGrantAll()
            _rootGranting.value = false
        }
    }

    /**
     * Grant ALL missing permissions via ADB shell commands (no root needed).
     * Requires [isAdbConnected] to be true.
     */
    fun grantAllViaAdb() {
        viewModelScope.launch {
            _rootGranting.value = true   // re-use the same loading indicator
            permissionManager.adbGrantAll()
            _rootGranting.value = false
        }
    }

    /**
     * Connect to the local ADB daemon at [host]:[port].
     * Call this when the user taps "Connect ADB" in the setup banner.
     */
    fun connectAdb(host: String = "localhost", port: Int = 5555) {
        viewModelScope.launch {
            adbManager.connect(host, port)
        }
    }

    /**
     * Start the sequential special-permission wizard.
     * The screen will call [advanceSpecialGrant] on every ON_RESUME until all
     * Settings-based special permissions are handled or the user stops.
     */
    fun startAutoGrantMode() { _autoGrantMode.value = true }

    /**
     * Immediately opens the system dialogs for SPECIAL permissions that show an
     * inline dialog (not a separate Settings page) — specifically battery-optimization
     * exemption and device-admin activation.  These can be shown right away without
     * navigation, so they don't need the sequential wizard loop.
     */
    fun grantDialogSpecialPermissions(entries: List<PermissionEntry>) {
        entries
            .filter { it.status != PermissionStatus.GRANTED
                    && it.tier == PermissionTier.SPECIAL
                    && it.id in DIALOG_SPECIAL_IDS }
            .forEach { permissionManager.openSpecialSettings(context, it) }
    }

    /**
     * Opens the next missing Settings-based special permission and returns `true`,
     * or stops auto-grant mode and returns `false` when nothing is left.
     *
     * Call this:
     * - From the "Grant All" button (to open the first Settings page).
     * - From the ON_RESUME lifecycle observer (to auto-advance the wizard).
     */
    fun advanceSpecialGrant(entries: List<PermissionEntry>): Boolean {
        val next = entries.firstOrNull {
            it.status != PermissionStatus.GRANTED
                    && it.tier == PermissionTier.SPECIAL
                    && it.id !in DIALOG_SPECIAL_IDS
        }
        return if (next != null) {
            permissionManager.openSpecialSettings(context, next)
            true
        } else {
            _autoGrantMode.value = false
            false
        }
    }

    /** Group entries by tier for display. */
    fun groupedByTier(entries: List<PermissionEntry>): Map<PermissionTier, List<PermissionEntry>> =
        entries.groupBy { it.tier }

    /** Count of granted / total entries. */
    fun summary(entries: List<PermissionEntry>): Pair<Int, Int> =
        entries.count { it.status == PermissionStatus.GRANTED } to entries.size

    private companion object {
        /**
         * Special permission IDs that show an inline system dialog rather than navigating
         * to a separate Settings page — these can be triggered all at once without
         * the sequential wizard loop.
         */
        val DIALOG_SPECIAL_IDS = setOf("battery_opt", "device_admin")
    }
}
