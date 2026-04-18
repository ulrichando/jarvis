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

    private val binder = LocalBinder()

    // ── Lifecycle ─────────────────────────────────────────────────────────

    override fun onCreate() {
        super.onCreate()
        Log.i(TAG, "onCreate")
        createNotificationChannel()
        // Use dataSync type — no dangerous permission required.
        // Microphone/camera/location types are added dynamically when those
        // permissions are granted (Android 14+ allows per-type promotion).
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIFICATION_ID,
                buildNotification(),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC,
            )
        } else {
            startForeground(NOTIFICATION_ID, buildNotification())
        }
        rootServiceConnection.bind()
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
