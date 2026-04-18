package com.jarvis.android.service

import android.app.Notification
import android.content.pm.PackageManager
import android.os.Build
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Captures all incoming status-bar notifications and re-emits them on
 * [JarvisNotificationListener.events] as [NotificationEvent] objects.
 *
 * Declared in AndroidManifest.xml as a `<service>` with
 * `android:permission="android.permission.BIND_NOTIFICATION_LISTENER_SERVICE"`.
 * The user must enable it in Settings → Apps → Special app access →
 * Notification access ([PermissionManager] handles the navigation).
 *
 * ## Hilt note
 * [NotificationListenerService] cannot be annotated with `@AndroidEntryPoint`
 * because it is bound by the system before Hilt's component is ready.
 * Instead, the companion [events] flow is a static singleton; consumers
 * (e.g. the AI tool layer) collect it without needing a direct reference.
 *
 * ## Usage
 * ```kotlin
 * JarvisNotificationListener.events.collect { event ->
 *     // feed to AI context or display in the notification panel
 * }
 * ```
 */
class JarvisNotificationListener : NotificationListenerService() {

    override fun onListenerConnected() {
        Log.i(TAG, "Notification listener connected")
        _connected.value = true
    }

    override fun onListenerDisconnected() {
        Log.i(TAG, "Notification listener disconnected")
        _connected.value = false
    }

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        val event = sbn.toEvent(posted = true) ?: return
        Log.d(TAG, "Posted: pkg=${event.packageName} title=${event.title?.take(40)}")
        _events.tryEmit(event)
    }

    override fun onNotificationRemoved(sbn: StatusBarNotification) {
        val event = sbn.toEvent(posted = false) ?: return
        Log.d(TAG, "Removed: pkg=${event.packageName} key=${event.key}")
        _events.tryEmit(event)
    }

    // ── Parsing ───────────────────────────────────────────────────────────

    private fun StatusBarNotification.toEvent(posted: Boolean): NotificationEvent? {
        return try {
            val extras = notification.extras
            val title  = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString()
            val text   = extras.getCharSequence(Notification.EXTRA_TEXT)?.toString()
                      ?: extras.getCharSequence(Notification.EXTRA_BIG_TEXT)?.toString()

            val appLabel = try {
                packageManager
                    .getApplicationLabel(
                        packageManager.getApplicationInfo(packageName, 0)
                    ).toString()
            } catch (_: PackageManager.NameNotFoundException) { packageName }

            NotificationEvent(
                key         = key,
                packageName = packageName,
                appLabel    = appLabel,
                title       = title,
                text        = text,
                category    = notification.category,
                posted      = posted,
                postTime    = postTime,
                isOngoing   = (notification.flags and Notification.FLAG_ONGOING_EVENT) != 0,
                isClearable = isClearable,
            )
        } catch (e: Exception) {
            Log.w(TAG, "Failed to parse notification: ${e.message}")
            null
        }
    }

    companion object {
        private const val TAG = "JarvisNotificationListener"

        private val _connected = MutableStateFlow(false)
        /** True when the listener is connected to the notification service. */
        val connected: StateFlow<Boolean> = _connected.asStateFlow()

        private val _events = MutableSharedFlow<NotificationEvent>(
            extraBufferCapacity = 64,
            onBufferOverflow = kotlinx.coroutines.channels.BufferOverflow.DROP_OLDEST,
        )
        /**
         * Stream of notification events. Subscribers receive both posted and
         * removed events from all apps. Buffer holds last 64 events so
         * late subscribers can catch up briefly.
         */
        val events: SharedFlow<NotificationEvent> = _events.asSharedFlow()
    }
}

// ── Data type ─────────────────────────────────────────────────────────────────

/**
 * Parsed representation of a [StatusBarNotification].
 *
 * @param posted     True = notification appeared; false = notification dismissed/removed.
 * @param isOngoing  True for persistent notifications (e.g. media playback, calls).
 */
data class NotificationEvent(
    val key:         String,
    val packageName: String,
    val appLabel:    String,
    val title:       String?,
    val text:        String?,
    val category:    String?,
    val posted:      Boolean,
    val postTime:    Long,
    val isOngoing:   Boolean,
    val isClearable: Boolean,
)
