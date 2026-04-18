package com.jarvis.android.system.permissions

import android.Manifest
import android.accessibilityservice.AccessibilityServiceInfo
import android.app.Activity
import android.app.AppOpsManager
import android.app.NotificationManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.os.PowerManager
import android.provider.Settings
import android.view.accessibility.AccessibilityManager
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import android.util.Log
import com.jarvis.android.system.adb.AdbManager
import com.jarvis.android.system.root.RootManager
import com.jarvis.android.system.root.RootShell
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Unified permission management for all three tiers of Android permissions:
 *
 * **Tier 1 — Dangerous runtime permissions**
 *   Granted via [ActivityCompat.requestPermissions]. Checked against
 *   [ContextCompat.checkSelfPermission]. Covers camera, mic, location,
 *   contacts, phone, storage, sensors, Bluetooth, and notifications.
 *
 * **Tier 2 — Special (appops) permissions**
 *   Each requires navigating to a dedicated Settings screen. Cannot be
 *   batch-requested. Examples: SYSTEM_ALERT_WINDOW, WRITE_SETTINGS,
 *   MANAGE_EXTERNAL_STORAGE, REQUEST_INSTALL_PACKAGES, battery optimization
 *   exemption, notification listener, accessibility service, device admin.
 *
 * **Tier 3 — Root**
 *   Managed by [RootManager]. Exposed here as a synthetic entry so the
 *   permission matrix screen shows all tiers in a single list.
 *
 * ## Usage
 * ```kotlin
 * // Observe the full permission matrix
 * permissionManager.permissions.collectAsState()
 *
 * // Refresh statuses (call from onResume)
 * permissionManager.refresh()
 *
 * // Request a dangerous permission
 * permissionManager.requestDangerous(activity, listOf(CAMERA_ENTRY.manifestName))
 *
 * // Open settings for a special permission
 * permissionManager.openSpecialSettings(context, OVERLAY_ENTRY)
 * ```
 *
 * The [PermissionMatrixScreen] collects [permissions] and renders each entry
 * grouped by [PermissionTier], with status icons and "Grant" buttons.
 */
@Singleton
class PermissionManager @Inject constructor(
    @ApplicationContext private val context: Context,
    private val rootManager: RootManager,
    private val rootShell: RootShell,
    private val adbManager: AdbManager,
) {

    private val _permissions = MutableStateFlow<List<PermissionEntry>>(buildEntries())
    val permissions: StateFlow<List<PermissionEntry>> = _permissions.asStateFlow()

    /** Re-evaluate all permission statuses (call from Activity/Fragment onResume). */
    fun refresh() {
        _permissions.update { entries -> entries.map { it.copy(status = checkStatus(it)) } }
    }

    /**
     * Returns the subset of [permissions] that are not yet granted.
     * Useful for the onboarding flow to know what still needs attention.
     */
    fun missing(): List<PermissionEntry> =
        _permissions.value.filter { it.status != PermissionStatus.GRANTED }

    /** True if every permission in [tier] is granted. */
    fun tierComplete(tier: PermissionTier): Boolean =
        _permissions.value.filter { it.tier == tier }.all { it.status == PermissionStatus.GRANTED }

    /** True when the device has an active root shell (Magisk / KernelSU / other su). */
    val isRooted: Boolean get() = rootManager.isRooted

    /** True when JARVIS is connected to the local ADB daemon via TCP. */
    val isAdbConnected: Boolean get() = adbManager.isConnected

    // ── Silent root-based grant ───────────────────────────────────────────

    /**
     * Grants ALL missing permissions silently using root shell commands.
     *
     * - Dangerous  → `pm grant <pkg> <manifest.permission.*>`
     * - Special    → per-permission `appops` / `cmd` / `settings` / `dpm` command
     * - Root       → already granted by definition if we are here
     *
     * This is only effective when [isRooted] is true.  Call [refresh] afterwards
     * (already done internally) to update the UI.
     */
    suspend fun rootGrantAll() {
        val pkg = context.packageName
        val missing = _permissions.value.filter { it.status != PermissionStatus.GRANTED }

        // ── Dangerous: pm grant ───────────────────────────────────────────
        missing
            .filter { it.tier == PermissionTier.DANGEROUS }
            .mapNotNull { it.manifestName }
            .filter { it.isNotBlank() }
            .distinct()
            .forEach { manifest ->
                val result = rootShell.exec("pm grant $pkg $manifest", asRoot = true)
                if (!result.isSuccess) Log.w(TAG, "pm grant failed for $manifest: ${result.outputText}")
            }

        // ── Special: per-permission root commands ─────────────────────────
        missing
            .filter { it.tier == PermissionTier.SPECIAL }
            .forEach { entry ->
                val cmd = rootCommandForSpecial(pkg, entry.id) ?: return@forEach
                val result = rootShell.exec(cmd, asRoot = true)
                if (!result.isSuccess) Log.w(TAG, "root grant failed for ${entry.id}: ${result.outputText}")
            }

        refresh()
    }

    /**
     * Maps a special permission ID to its root shell command.
     * Returns null for permissions that cannot be granted via shell (or don't need to be).
     */
    private fun rootCommandForSpecial(pkg: String, id: String): String? = when (id) {
        "overlay"               -> "appops set $pkg SYSTEM_ALERT_WINDOW allow"
        "write_settings"        -> "appops set $pkg WRITE_SETTINGS allow"
        "manage_storage"        -> "appops set $pkg MANAGE_EXTERNAL_STORAGE allow"
        "install_packages"      -> "appops set $pkg REQUEST_INSTALL_PACKAGES allow"
        "battery_opt"           -> "dumpsys deviceidle whitelist +$pkg"
        "notification_listener" ->
            "cmd notification allow_listener $pkg/com.jarvis.android.service.JarvisNotificationListener"
        "accessibility"         ->
            // Write the component into the secure settings and enable accessibility globally
            "settings put secure enabled_accessibility_services " +
            "$pkg/com.jarvis.android.service.JarvisAccessibilityService && " +
            "settings put secure accessibility_enabled 1"
        "device_admin"          ->
            "dpm set-active-admin --user 0 $pkg/com.jarvis.android.service.JarvisDeviceAdmin"
        "usage_stats"           -> "appops set $pkg GET_USAGE_STATS allow"
        "nls_dnd"               -> "cmd notification allow_dnd $pkg"
        else                    -> null
    }

    // ── Silent ADB-based grant (same commands as root but via adb shell) ─────

    /**
     * Grant ALL missing permissions using `adb shell` commands via the local ADB daemon.
     *
     * The `shell` user (uid 2000) can run `pm grant`, `appops set`, `settings put`,
     * `cmd notification`, and `dpm set-active-admin` — everything needed to grant
     * every permission tier without root.
     *
     * Only effective when [isAdbConnected] is true.
     */
    suspend fun adbGrantAll() {
        val pkg     = context.packageName
        val missing = _permissions.value.filter { it.status != PermissionStatus.GRANTED }

        // Dangerous permissions via pm grant
        missing
            .filter { it.tier == PermissionTier.DANGEROUS }
            .mapNotNull { it.manifestName }
            .filter { it.isNotBlank() }
            .distinct()
            .forEach { manifest ->
                val r = adbManager.exec("pm grant $pkg $manifest")
                if (!r.isSuccess) Log.w(TAG, "adb pm grant failed $manifest: ${r.output}")
            }

        // Special permissions — same commands as rootGrantAll
        missing
            .filter { it.tier == PermissionTier.SPECIAL }
            .forEach { entry ->
                val cmd = rootCommandForSpecial(pkg, entry.id) ?: return@forEach
                val r   = adbManager.exec(cmd)
                if (!r.isSuccess) Log.w(TAG, "adb special grant failed ${entry.id}: ${r.output}")
            }

        refresh()
    }

    // ── Dangerous permission request ──────────────────────────────────────

    /**
     * Forward to [ActivityCompat.requestPermissions].
     * Must be called from an [Activity] context.
     * Call [refresh] from `onRequestPermissionsResult` to update statuses.
     *
     * @param activity     The foreground activity.
     * @param permissions  Manifest permission strings (e.g. [Manifest.permission.CAMERA]).
     * @param requestCode  Passed through to `onRequestPermissionsResult`.
     */
    fun requestDangerous(
        activity: Activity,
        permissions: List<String>,
        requestCode: Int = REQUEST_CODE_DANGEROUS,
    ) {
        ActivityCompat.requestPermissions(activity, permissions.toTypedArray(), requestCode)
    }

    /**
     * Open the OS settings screen for a [PermissionTier.SPECIAL] entry.
     * The user must grant it manually; call [refresh] when they return.
     */
    fun openSpecialSettings(context: Context, entry: PermissionEntry) {
        val intent = specialSettingsIntent(entry) ?: return
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(intent)
    }

    /** Open the app's full permission settings page in system Settings. */
    fun openAppSettings(context: Context) {
        val intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
            data = Uri.fromParts("package", context.packageName, null)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        context.startActivity(intent)
    }

    // ── Status evaluation ─────────────────────────────────────────────────

    private fun checkStatus(entry: PermissionEntry): PermissionStatus = when (entry.tier) {
        PermissionTier.DANGEROUS -> checkDangerous(entry.manifestName!!)
        PermissionTier.SPECIAL   -> checkSpecial(entry)
        PermissionTier.ROOT      -> if (rootManager.isRooted) PermissionStatus.GRANTED
                                    else PermissionStatus.DENIED
    }

    private fun checkDangerous(manifestName: String): PermissionStatus =
        if (ContextCompat.checkSelfPermission(context, manifestName) ==
            PackageManager.PERMISSION_GRANTED) PermissionStatus.GRANTED
        else PermissionStatus.DENIED

    @Suppress("DEPRECATION")
    private fun checkSpecial(entry: PermissionEntry): PermissionStatus = when (entry.id) {
        ID_OVERLAY -> if (Settings.canDrawOverlays(context)) PermissionStatus.GRANTED
                      else PermissionStatus.DENIED

        ID_WRITE_SETTINGS -> if (Settings.System.canWrite(context)) PermissionStatus.GRANTED
                             else PermissionStatus.DENIED

        ID_MANAGE_STORAGE -> if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            if (Environment.isExternalStorageManager()) PermissionStatus.GRANTED
            else PermissionStatus.DENIED
        } else PermissionStatus.GRANTED   // not needed before R

        ID_INSTALL_PACKAGES -> {
            val pm = context.packageManager
            val granted = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                pm.canRequestPackageInstalls()
            } else true
            if (granted) PermissionStatus.GRANTED else PermissionStatus.DENIED
        }

        ID_BATTERY_OPT -> {
            val pm = context.getSystemService(Context.POWER_SERVICE) as PowerManager
            if (pm.isIgnoringBatteryOptimizations(context.packageName)) PermissionStatus.GRANTED
            else PermissionStatus.DENIED
        }

        ID_NOTIFICATION_LISTENER -> {
            val flat = Settings.Secure.getString(
                context.contentResolver,
                "enabled_notification_listeners",
            ) ?: ""
            val component = ComponentName(context, "com.jarvis.android.service.JarvisNotificationListener")
            if (flat.contains(component.flattenToString())) PermissionStatus.GRANTED
            else PermissionStatus.DENIED
        }

        ID_ACCESSIBILITY -> {
            val am = context.getSystemService(Context.ACCESSIBILITY_SERVICE) as AccessibilityManager
            val enabled = am.getEnabledAccessibilityServiceList(AccessibilityServiceInfo.FEEDBACK_ALL_MASK)
            val component = ComponentName(context, "com.jarvis.android.service.JarvisAccessibilityService")
            if (enabled.any { it.resolveInfo.serviceInfo.packageName == component.packageName &&
                               it.resolveInfo.serviceInfo.name == component.className })
                PermissionStatus.GRANTED else PermissionStatus.DENIED
        }

        ID_DEVICE_ADMIN -> {
            val dpm = context.getSystemService(Context.DEVICE_POLICY_SERVICE) as
                android.app.admin.DevicePolicyManager
            val admin = ComponentName(context, "com.jarvis.android.service.JarvisDeviceAdmin")
            if (dpm.isAdminActive(admin)) PermissionStatus.GRANTED else PermissionStatus.DENIED
        }

        ID_USAGE_STATS -> {
            val appOps = context.getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
            val mode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                appOps.unsafeCheckOpNoThrow(
                    AppOpsManager.OPSTR_GET_USAGE_STATS,
                    android.os.Process.myUid(),
                    context.packageName,
                )
            } else {
                @Suppress("DEPRECATION")
                appOps.checkOpNoThrow(
                    AppOpsManager.OPSTR_GET_USAGE_STATS,
                    android.os.Process.myUid(),
                    context.packageName,
                )
            }
            if (mode == AppOpsManager.MODE_ALLOWED) PermissionStatus.GRANTED
            else PermissionStatus.DENIED
        }

        ID_NLS_DND -> {
            val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && nm.isNotificationPolicyAccessGranted)
                PermissionStatus.GRANTED else PermissionStatus.DENIED
        }

        else -> PermissionStatus.UNKNOWN
    }

    // ── Settings intent factory ───────────────────────────────────────────

    private fun specialSettingsIntent(entry: PermissionEntry): Intent? = when (entry.id) {
        ID_OVERLAY -> Intent(
            Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
            Uri.fromParts("package", context.packageName, null),
        )
        ID_WRITE_SETTINGS -> Intent(
            Settings.ACTION_MANAGE_WRITE_SETTINGS,
            Uri.fromParts("package", context.packageName, null),
        )
        ID_MANAGE_STORAGE -> if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION).apply {
                data = Uri.fromParts("package", context.packageName, null)
            }
        } else null
        ID_INSTALL_PACKAGES -> if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES).apply {
                data = Uri.fromParts("package", context.packageName, null)
            }
        } else null
        ID_BATTERY_OPT -> Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).apply {
            data = Uri.fromParts("package", context.packageName, null)
        }
        ID_NOTIFICATION_LISTENER -> Intent("android.settings.ACTION_NOTIFICATION_LISTENER_SETTINGS")
        ID_ACCESSIBILITY          -> Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)
        ID_DEVICE_ADMIN           -> Intent(android.app.admin.DevicePolicyManager.ACTION_ADD_DEVICE_ADMIN).apply {
            putExtra(android.app.admin.DevicePolicyManager.EXTRA_DEVICE_ADMIN,
                ComponentName(context, "com.jarvis.android.service.JarvisDeviceAdmin"))
        }
        ID_USAGE_STATS -> Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS)
        ID_NLS_DND     -> Intent(Settings.ACTION_NOTIFICATION_POLICY_ACCESS_SETTINGS)
        else           -> null
    }

    // ── Entry catalogue ───────────────────────────────────────────────────

    private fun buildEntries(): List<PermissionEntry> = listOf(

        // ── Tier 1: Dangerous ──────────────────────────────────────────────
        PermissionEntry(
            id           = "camera",
            displayName  = "Camera",
            description  = "Take photos and video for AI vision features",
            tier         = PermissionTier.DANGEROUS,
            manifestName = Manifest.permission.CAMERA,
            isRequired   = false,
        ),
        PermissionEntry(
            id           = "record_audio",
            displayName  = "Microphone",
            description  = "Voice input for hands-free AI commands",
            tier         = PermissionTier.DANGEROUS,
            manifestName = Manifest.permission.RECORD_AUDIO,
            isRequired   = false,
        ),
        PermissionEntry(
            id           = "fine_location",
            displayName  = "Precise Location",
            description  = "GPS coordinates for location-aware tools",
            tier         = PermissionTier.DANGEROUS,
            manifestName = Manifest.permission.ACCESS_FINE_LOCATION,
            isRequired   = false,
        ),
        PermissionEntry(
            id           = "coarse_location",
            displayName  = "Approximate Location",
            description  = "Network-based location for WiFi scanning",
            tier         = PermissionTier.DANGEROUS,
            manifestName = Manifest.permission.ACCESS_COARSE_LOCATION,
            isRequired   = false,
        ),
        PermissionEntry(
            id           = "bg_location",
            displayName  = "Background Location",
            description  = "Location access when app is in background",
            tier         = PermissionTier.DANGEROUS,
            manifestName = Manifest.permission.ACCESS_BACKGROUND_LOCATION,
            isRequired   = false,
        ),
        PermissionEntry(
            id           = "read_contacts",
            displayName  = "Read Contacts",
            description  = "Access contact list for AI assistant context",
            tier         = PermissionTier.DANGEROUS,
            manifestName = Manifest.permission.READ_CONTACTS,
            isRequired   = false,
        ),
        PermissionEntry(
            id           = "read_call_log",
            displayName  = "Call Log",
            description  = "Read call history for the AI assistant",
            tier         = PermissionTier.DANGEROUS,
            manifestName = Manifest.permission.READ_CALL_LOG,
            isRequired   = false,
        ),
        PermissionEntry(
            id           = "read_sms",
            displayName  = "Read SMS",
            description  = "Read SMS messages for AI assistant context",
            tier         = PermissionTier.DANGEROUS,
            manifestName = Manifest.permission.READ_SMS,
            isRequired   = false,
        ),
        PermissionEntry(
            id           = "read_phone_state",
            displayName  = "Phone State",
            description  = "Read device identifiers and call state",
            tier         = PermissionTier.DANGEROUS,
            manifestName = Manifest.permission.READ_PHONE_STATE,
            isRequired   = false,
        ),
        PermissionEntry(
            id           = "bluetooth_scan",
            displayName  = "Bluetooth Scan",
            description  = "Scan for nearby Bluetooth devices",
            tier         = PermissionTier.DANGEROUS,
            manifestName = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S)
                               Manifest.permission.BLUETOOTH_SCAN
                           else Manifest.permission.BLUETOOTH,
            isRequired   = false,
        ),
        PermissionEntry(
            id           = "bluetooth_connect",
            displayName  = "Bluetooth Connect",
            description  = "Connect to paired Bluetooth devices",
            tier         = PermissionTier.DANGEROUS,
            manifestName = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S)
                               Manifest.permission.BLUETOOTH_CONNECT
                           else Manifest.permission.BLUETOOTH,
            isRequired   = false,
        ),
        PermissionEntry(
            id           = "post_notifications",
            displayName  = "Notifications",
            description  = "Show foreground service and alert notifications",
            tier         = PermissionTier.DANGEROUS,
            manifestName = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU)
                               Manifest.permission.POST_NOTIFICATIONS
                           else "",   // auto-granted below T
            isRequired   = true,
        ),
        PermissionEntry(
            id           = "body_sensors",
            displayName  = "Body Sensors",
            description  = "Read heart rate and health sensor data",
            tier         = PermissionTier.DANGEROUS,
            manifestName = Manifest.permission.BODY_SENSORS,
            isRequired   = false,
        ),
        PermissionEntry(
            id           = "activity_recognition",
            displayName  = "Activity Recognition",
            description  = "Detect physical activity (step counter, etc.)",
            tier         = PermissionTier.DANGEROUS,
            manifestName = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q)
                               Manifest.permission.ACTIVITY_RECOGNITION
                           else "",
            isRequired   = false,
        ),
        PermissionEntry(
            id           = "nearby_wifi",
            displayName  = "Nearby WiFi Networks",
            description  = "Scan visible 802.11 access points",
            tier         = PermissionTier.DANGEROUS,
            manifestName = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU)
                               Manifest.permission.NEARBY_WIFI_DEVICES
                           else Manifest.permission.ACCESS_FINE_LOCATION,
            isRequired   = false,
        ),

        // ── Tier 2: Special ────────────────────────────────────────────────
        PermissionEntry(
            id          = ID_OVERLAY,
            displayName = "Display Over Other Apps",
            description = "Float the JARVIS HUD above other applications",
            tier        = PermissionTier.SPECIAL,
            isRequired  = false,
        ),
        PermissionEntry(
            id          = ID_WRITE_SETTINGS,
            displayName = "Modify System Settings",
            description = "Adjust brightness, ringtone, and system flags",
            tier        = PermissionTier.SPECIAL,
            isRequired  = false,
        ),
        PermissionEntry(
            id          = ID_MANAGE_STORAGE,
            displayName = "Manage All Files",
            description = "Full filesystem access (MANAGE_EXTERNAL_STORAGE)",
            tier        = PermissionTier.SPECIAL,
            isRequired  = false,
        ),
        PermissionEntry(
            id          = ID_INSTALL_PACKAGES,
            displayName = "Install Unknown APKs",
            description = "Sideload APKs delivered by the AI agent",
            tier        = PermissionTier.SPECIAL,
            isRequired  = false,
        ),
        PermissionEntry(
            id          = ID_BATTERY_OPT,
            displayName = "Battery Optimization Exempt",
            description = "Keep the foreground service alive indefinitely",
            tier        = PermissionTier.SPECIAL,
            isRequired  = true,
        ),
        PermissionEntry(
            id          = ID_NOTIFICATION_LISTENER,
            displayName = "Notification Listener",
            description = "Read all incoming notifications",
            tier        = PermissionTier.SPECIAL,
            isRequired  = false,
        ),
        PermissionEntry(
            id          = ID_ACCESSIBILITY,
            displayName = "Accessibility Service",
            description = "Observe and interact with any screen content",
            tier        = PermissionTier.SPECIAL,
            isRequired  = false,
        ),
        PermissionEntry(
            id          = ID_DEVICE_ADMIN,
            displayName = "Device Administrator",
            description = "Lock screen, wipe, and enforce device policies",
            tier        = PermissionTier.SPECIAL,
            isRequired  = false,
        ),
        PermissionEntry(
            id          = ID_USAGE_STATS,
            displayName = "Usage Access",
            description = "Read app usage statistics and foreground history",
            tier        = PermissionTier.SPECIAL,
            isRequired  = false,
        ),
        PermissionEntry(
            id          = ID_NLS_DND,
            displayName = "Do Not Disturb Access",
            description = "Toggle DND mode and manage notification policy",
            tier        = PermissionTier.SPECIAL,
            isRequired  = false,
        ),

        // ── Tier 3: Root ───────────────────────────────────────────────────
        PermissionEntry(
            id          = "root_shell",
            displayName = "Root Shell (su)",
            description = "Full root via Magisk / KernelSU",
            tier        = PermissionTier.ROOT,
            isRequired  = false,
        ),
    )

    private companion object {
        const val TAG                    = "JarvisPermissions"
        const val REQUEST_CODE_DANGEROUS = 1001

        // Special-permission IDs (stable, used as map keys)
        const val ID_OVERLAY              = "overlay"
        const val ID_WRITE_SETTINGS       = "write_settings"
        const val ID_MANAGE_STORAGE       = "manage_storage"
        const val ID_INSTALL_PACKAGES     = "install_packages"
        const val ID_BATTERY_OPT          = "battery_opt"
        const val ID_NOTIFICATION_LISTENER = "notification_listener"
        const val ID_ACCESSIBILITY        = "accessibility"
        const val ID_DEVICE_ADMIN         = "device_admin"
        const val ID_USAGE_STATS          = "usage_stats"
        const val ID_NLS_DND              = "nls_dnd"
    }
}

// ── Data types ────────────────────────────────────────────────────────────────

/**
 * Three tiers of Android permission, rendered as distinct sections in
 * [PermissionMatrixScreen].
 */
enum class PermissionTier {
    /** Granted via [ActivityCompat.requestPermissions]. */
    DANGEROUS,
    /** Granted by navigating to a dedicated Settings screen. */
    SPECIAL,
    /** Granted by the device's root manager (Magisk / KernelSU). */
    ROOT,
}

/** Current grant status of a single [PermissionEntry]. */
enum class PermissionStatus {
    GRANTED,
    DENIED,
    UNKNOWN,
}

/**
 * A single row in the permission matrix.
 *
 * @param id           Stable identifier used by the ViewModel.
 * @param displayName  Human-readable name shown in the UI.
 * @param description  One-line explanation shown as subtitle.
 * @param tier         Which request mechanism is required.
 * @param manifestName `Manifest.permission.*` string — null for SPECIAL / ROOT entries.
 * @param isRequired   Required for core functionality (shown with a warning badge).
 * @param status       Current grant state (refreshed by [PermissionManager.refresh]).
 */
data class PermissionEntry(
    val id:           String,
    val displayName:  String,
    val description:  String,
    val tier:         PermissionTier,
    val manifestName: String? = null,
    val isRequired:   Boolean = false,
    val status:       PermissionStatus = PermissionStatus.UNKNOWN,
)
