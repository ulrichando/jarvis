package com.jarvis.android.startup

import android.content.Context
import android.util.Log
import androidx.startup.Initializer

/**
 * App Startup initializer that runs synchronously during [ContentProvider] init,
 * before [Application.onCreate].
 *
 * Responsibilities:
 *   - Log startup for crash-report correlation.
 *   - Anything that must happen before the first Activity is created and
 *     that doesn't depend on Hilt (which isn't available this early).
 *
 * Heavy initialisation (libsu, Hilt components, Room) happens later in
 * [JarvisApplication.onCreate] or on first use.
 */
class JarvisInitializer : Initializer<Unit> {

    override fun create(context: Context) {
        Log.i(TAG, "JARVIS initializing — process=${android.os.Process.myPid()}")
    }

    /** No dependencies on other [Initializer]s. */
    override fun dependencies(): List<Class<out Initializer<*>>> = emptyList()

    private companion object {
        const val TAG = "JarvisInitializer"
    }
}
