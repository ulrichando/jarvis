package com.jarvis.android.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Binder
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.lifecycle.LifecycleService
import androidx.lifecycle.lifecycleScope
import com.jarvis.android.R
import com.jarvis.android.system.bridge.JarvisLoopbackServer
import com.jarvis.android.system.root.RootServiceConnection
import com.jarvis.android.system.terminal.TerminalSessionManager
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * Long-lived foreground service that keeps the JARVIS process alive indefinitely.
 *
 * Responsibilities:
 *   - Display the persistent status-bar notification required for foreground services
 *   - Hold [RootServiceConnection] alive so PTY sessions survive app backgrounding
 *   - Hold [TerminalSessionManager] — its sessions survive screen rotation because
 *     the service (not the ViewModel) owns them
 *   - Provide a [LocalBinder] so bound components can confirm the service is running
 *
 * Foreground service type (declared in AndroidManifest.xml):
 *   `camera | microphone | location | dataSync | specialUse`
 *
 * Lifecycle:
 *   - Started by [BootReceiver] on device boot
 *   - Started by [JarvisApplication] at app launch
 *   - Survives app backgrounding; killed only when the user explicitly stops it
 *     or the system pressure-kills the process
 *
 * Stopping: send [ACTION_STOP] as an intent action or call [stopSelf] from within.
 */
@AndroidEntryPoint
class JarvisForegroundService : LifecycleService() {

    @Inject lateinit var rootServiceConnection: RootServiceConnection
    @Inject lateinit var terminalSessionManager: TerminalSessionManager
    @Inject lateinit var loopbackServer: JarvisLoopbackServer

    private val binder = LocalBinder()

    // ── Lifecycle ─────────────────────────────────────────────────────────

    override fun onCreate() {
        super.onCreate()
        Log.i(TAG, "onCreate")
        createNotificationChannel()
        // FGS type MUST match what the manifest declares — Android 14+
        // enforces that the runtime value is a subset of the manifest
        // attribute. AndroidManifest declares `specialUse` (with the
        // PROPERTY_SPECIAL_USE_FGS_SUBTYPE marker), because this is a
        // catch-all AI orchestration service that doesn't fit any of the
        // predefined categories — and `specialUse` is also the only FGS
        // type Android lets us start from BOOT_COMPLETED /
        // MY_PACKAGE_REPLACED contexts without user interaction.
        //
        // SPECIAL_USE was introduced in API 34 (Android 14). Fall back to
        // DATA_SYNC on 29–33 (still valid there), and to the typeless
        // overload below 29.
        when {
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE /* 34 */ -> {
                startForeground(
                    NOTIFICATION_ID,
                    buildNotification(),
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE,
                )
            }
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q /* 29 */ -> {
                startForeground(
                    NOTIFICATION_ID,
                    buildNotification(),
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC,
                )
            }
            else -> {
                startForeground(NOTIFICATION_ID, buildNotification())
            }
        }
        rootServiceConnection.bind()
        // Loopback HTTP+SSE bridge so the on-device terminal's `jarvis`
        // command can drive the real agent loop (tools + streaming) by
        // POSTing to 127.0.0.1:47811/chat. See JarvisLoopbackServer.
        loopbackServer.start()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        if (intent?.action == ACTION_STOP) {
            Log.i(TAG, "Stop action received")
            stopSelf()
            return START_NOT_STICKY
        }
        Log.i(TAG, "onStartCommand — service running")
        return START_STICKY
    }

    override fun onBind(intent: Intent): IBinder {
        super.onBind(intent)
        return binder
    }

    override fun onDestroy() {
        Log.i(TAG, "onDestroy — cleaning up sessions and root service")
        loopbackServer.stop()
        lifecycleScope.launch {
            terminalSessionManager.killAll()
        }
        rootServiceConnection.unbind()
        super.onDestroy()
    }

    // ── Notification ──────────────────────────────────────────────────────

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            "JARVIS Service",
            NotificationManager.IMPORTANCE_LOW,   // silent, no sound/vibration
        ).apply {
            description      = "Keeps JARVIS running in the background"
            setShowBadge(false)
            lockscreenVisibility = Notification.VISIBILITY_SECRET
        }
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(channel)
    }

    private fun buildNotification(): Notification {
        val launchIntent = packageManager
            .getLaunchIntentForPackage(packageName)
            ?.apply { flags = Intent.FLAG_ACTIVITY_SINGLE_TOP }
        val contentPi = PendingIntent.getActivity(
            this, 0, launchIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        val stopPi = PendingIntent.getService(
            this, 1,
            Intent(this, JarvisForegroundService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("JARVIS")
            .setContentText("AI assistant is active")
            .setSmallIcon(R.drawable.ic_jarvis_notification)
            .setContentIntent(contentPi)
            .addAction(0, "Stop", stopPi)
            .setOngoing(true)
            .setSilent(true)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .build()
    }

    // ── Binder ────────────────────────────────────────────────────────────

    inner class LocalBinder : Binder() {
        val service: JarvisForegroundService get() = this@JarvisForegroundService
    }

    companion object {
        private const val TAG           = "JarvisForegroundService"
        private const val CHANNEL_ID    = "jarvis_service"
        private const val NOTIFICATION_ID = 1
        const val ACTION_STOP           = "com.jarvis.android.ACTION_STOP_SERVICE"

        fun startIntent(context: Context) =
            Intent(context, JarvisForegroundService::class.java)
    }
}
