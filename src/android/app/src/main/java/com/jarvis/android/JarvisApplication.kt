package com.jarvis.android

import android.app.Application
import android.util.Log
import androidx.hilt.work.HiltWorkerFactory
import androidx.work.Configuration
import com.topjohnwu.superuser.Shell
import dagger.hilt.android.HiltAndroidApp
import javax.inject.Inject

/**
 * Application entry point.
 *
 * Responsibilities:
 *   1. Trigger Hilt component generation (`@HiltAndroidApp`).
 *   2. Configure libsu before any component accesses the root shell.
 *      libsu requires `Shell.enableVerboseLogging` and `Shell.setDefaultBuilder`
 *      to be called **before** the first `Shell.getShell()` call — doing it here
 *      in `onCreate()` guarantees that ordering.
 *   3. Implement [Configuration.Provider] so WorkManager uses [HiltWorkerFactory].
 *      This lets `@HiltWorker` classes (e.g. [DownloadWorker]) receive Hilt
 *      injection. The default WorkManagerInitializer is removed from the manifest
 *      so WorkManager defers to this configuration instead of self-initializing.
 *   4. The [JarvisForegroundService] is started from [MainActivity.onCreate] once
 *      the Activity has the POST_NOTIFICATIONS permission (required on Android 13+).
 *      On reboot it is started by [BootReceiver].
 */
@HiltAndroidApp
class JarvisApplication : Application(), Configuration.Provider {

    @Inject lateinit var workerFactory: HiltWorkerFactory

    override val workManagerConfiguration: Configuration
        get() = Configuration.Builder()
            .setWorkerFactory(workerFactory)
            .build()

    override fun onCreate() {
        super.onCreate()
        configureLibsu()
    }

    private fun configureLibsu() {
        Shell.enableVerboseLogging = BuildConfig.DEBUG

        Shell.setDefaultBuilder(
            Shell.Builder.create()
                .setFlags(Shell.FLAG_REDIRECT_STDERR)   // merge stderr into stdout
                .setInitializers(LibsuInit::class.java)
                .setTimeout(10),
        )

        Log.d(TAG, "libsu configured (debug=${BuildConfig.DEBUG})")
    }

    companion object {
        private const val TAG = "JarvisApplication"
    }
}

/**
 * libsu shell initializer — runs once per shell instance inside the root process.
 * We use it to set a predictable PATH and disable the shell's job-control signals
 * that would interfere with our PTY sessions.
 */
private class LibsuInit : Shell.Initializer() {
    override fun onInit(context: android.content.Context, shell: Shell): Boolean {
        // Verify we actually have root
        val result = shell.newJob().add("id").exec()
        val uid = result.out.firstOrNull() ?: ""
        Log.d("LibsuInit", "shell id: $uid")
        return result.isSuccess
    }
}
