package com.jarvis.android

import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import com.jarvis.android.core.designsystem.JarvisTheme
import com.jarvis.android.navigation.JarvisNavGraph
import com.jarvis.android.service.JarvisForegroundService
import dagger.hilt.android.AndroidEntryPoint

/**
 * Single-activity host for the JARVIS Compose UI.
 *
 * Runs in full immersive mode — status bar and navigation bar are hidden.
 * Swiping down from the top reveals the status bar / notification shade
 * temporarily (system behaviour); swiping up from the bottom shows the
 * navigation bar temporarily. Both auto-hide again after a short delay.
 *
 * This matches the "video game / Google Maps" look the user requested:
 * the app occupies every pixel of the display.
 */
@AndroidEntryPoint
class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        installSplashScreen()
        super.onCreate(savedInstanceState)

        // Edge-to-edge: draw behind system bars
        enableEdgeToEdge()

        // Immersive sticky — hide both status bar and nav bar.
        // The user can still swipe down to see notifications; the bars
        // auto-hide again once they stop interacting with them.
        WindowCompat.setDecorFitsSystemWindows(window, false)
        WindowInsetsControllerCompat(window, window.decorView).apply {
            hide(WindowInsetsCompat.Type.systemBars())
            systemBarsBehavior =
                WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }

        // Keep screen on while JARVIS is in the foreground
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        startForegroundServiceIfNeeded()

        setContent {
            JarvisTheme {
                JarvisNavGraph(context = applicationContext)
            }
        }
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        // Re-apply immersive mode when focus returns (e.g. after a dialog closes)
        if (hasFocus) {
            WindowInsetsControllerCompat(window, window.decorView).apply {
                hide(WindowInsetsCompat.Type.systemBars())
                systemBarsBehavior =
                    WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            }
        }
    }

    // ── Foreground service ────────────────────────────────────────────────

    private fun startForegroundServiceIfNeeded() {
        val intent = Intent(this, JarvisForegroundService::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(intent)
        } else {
            startService(intent)
        }
    }
}
