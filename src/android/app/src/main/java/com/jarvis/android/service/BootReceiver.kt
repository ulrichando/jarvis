package com.jarvis.android.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log

/**
 * Starts [JarvisForegroundService] on device boot.
 *
 * Responds to two boot broadcasts so the service restarts regardless of
 * whether direct-boot (credential-encrypted) storage is ready:
 *
 *   - `android.intent.action.BOOT_COMPLETED`        — after user unlock
 *   - `android.intent.action.LOCKED_BOOT_COMPLETED` — at end of boot, before unlock (API 24+)
 *
 * Both are declared in AndroidManifest.xml with
 * `android:directBootAware="true"` on the receiver.
 *
 * The service itself starts with `startForegroundService` (required when
 * starting a foreground service from a background context on API 26+).
 */
class BootReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action ?: return
        if (action != Intent.ACTION_BOOT_COMPLETED &&
            action != "android.intent.action.LOCKED_BOOT_COMPLETED") return

        Log.i(TAG, "Boot received ($action) — starting JarvisForegroundService")

        val serviceIntent = JarvisForegroundService.startIntent(context)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.startForegroundService(serviceIntent)
        } else {
            context.startService(serviceIntent)
        }
    }

    private companion object {
        const val TAG = "JarvisBootReceiver"
    }
}
