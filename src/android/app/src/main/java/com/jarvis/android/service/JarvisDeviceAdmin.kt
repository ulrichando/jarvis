package com.jarvis.android.service

import android.app.admin.DeviceAdminReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Device Administrator receiver.
 *
 * Grants JARVIS the ability to:
 *   - Lock the screen programmatically (`DevicePolicyManager.lockNow()`)
 *   - Set maximum screen-off timeout
 *   - Wipe the device or external storage (requires explicit user confirmation
 *     via [JarvisToolDispatcher] before the AI can invoke this)
 *   - Enforce password policies (future feature)
 *
 * Declared in AndroidManifest.xml as a `<receiver>` with:
 *   `android:permission="android.permission.BIND_DEVICE_ADMIN"`
 * Uses policies defined in `res/xml/device_admin.xml`.
 *
 * The user activates this in Settings → Security → Device admin apps, or via
 * the [PermissionManager] which fires the [DevicePolicyManager.ACTION_ADD_DEVICE_ADMIN]
 * intent with the admin component pre-filled.
 *
 * ## Usage (from AI tool layer)
 * ```kotlin
 * if (JarvisDeviceAdmin.isActive) {
 *     dpm.lockNow()
 * }
 * ```
 */
class JarvisDeviceAdmin : DeviceAdminReceiver() {

    override fun onEnabled(context: Context, intent: Intent) {
        Log.i(TAG, "Device admin enabled")
        _isActive.value = true
    }

    override fun onDisabled(context: Context, intent: Intent) {
        Log.i(TAG, "Device admin disabled")
        _isActive.value = false
    }

    override fun onPasswordChanged(context: Context, intent: Intent) {
        Log.d(TAG, "Device password changed")
    }

    override fun onPasswordFailed(context: Context, intent: Intent) {
        Log.w(TAG, "Device password attempt failed")
    }

    override fun onPasswordSucceeded(context: Context, intent: Intent) {
        Log.d(TAG, "Device password attempt succeeded")
    }

    companion object {
        private const val TAG = "JarvisDeviceAdmin"

        private val _isActive = MutableStateFlow(false)
        /** True when JARVIS is an active device administrator. */
        val isActive: StateFlow<Boolean> = _isActive.asStateFlow()
    }
}
