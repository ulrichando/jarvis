package com.jarvis.android.presentation.chat

import android.graphics.Color
import android.view.View
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView

/**
 * Renders the same Three.js ArcReactor holographic sphere used on the desktop,
 * inside a hardware-accelerated WebView loading [arc_reactor.html] from assets.
 *
 * State is pushed to JavaScript via [WebView.evaluateJavascript] after page load.
 * The JS global [window.setJarvisState(state, audioLevel)] drives all animations:
 *   - idle     → slow rotation, green eye rings
 *   - speaking → blue rings, particle burst, fast neural arcs
 *   - thinking → yellow scan ring sweeps, faster globe spin
 *
 * @param isAiSpeaking  true while the AI is streaming a response or speaking via TTS
 * @param audioLevel    normalised amplitude [0, 1] from the Visualizer or simulated
 *                      sine; drives particle energy and glow intensity in JS
 * @param modifier      applied to the underlying [AndroidView]
 */
@Composable
fun ArcReactorWebView(
    isAiSpeaking: Boolean,
    audioLevel:   Float,
    modifier:     Modifier = Modifier,
) {
    val jsState   = if (isAiSpeaking) "speaking" else "idle"
    val pageReady = remember { mutableStateOf(false) }
    var webView: WebView? by remember { mutableStateOf(null) }

    // Push state updates to JS whenever inputs change or the page finishes loading.
    LaunchedEffect(jsState, audioLevel, pageReady.value) {
        if (!pageReady.value) return@LaunchedEffect
        webView?.evaluateJavascript(
            "window.setJarvisState && window.setJarvisState('$jsState', $audioLevel)",
            null,
        )
    }

    AndroidView(
        factory = { ctx ->
            WebView(ctx).apply {
                with(settings) {
                    @Suppress("SetJavaScriptEnabled")
                    javaScriptEnabled    = true   // required for Three.js
                    domStorageEnabled    = true
                    allowFileAccess      = true   // load arc_reactor.html from assets
                    // Do NOT set useWideViewPort/loadWithOverviewMode — they force a
                    // 980 px virtual viewport which makes window.innerWidth huge and
                    // pushes the Three.js camera back until the sphere is tiny.
                    useWideViewPort      = false
                    loadWithOverviewMode = false
                }
                // Hardware layer is required for WebGL compositing without tearing
                setLayerType(View.LAYER_TYPE_HARDWARE, null)
                // Transparent so VoiceBg (#232323) shows through behind the sphere
                setBackgroundColor(Color.TRANSPARENT)

                webViewClient = object : WebViewClient() {
                    override fun onPageFinished(view: WebView, url: String) {
                        pageReady.value = true
                    }
                }

                loadUrl("file:///android_asset/arc_reactor.html")
                webView = this
            }
        },
        modifier = modifier,
    )
}
