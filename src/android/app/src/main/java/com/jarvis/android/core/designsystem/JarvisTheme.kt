package com.jarvis.android.core.designsystem

import android.app.Activity
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat

// ── Composition Locals ────────────────────────────────────────────────────────

/**
 * Provides extended JARVIS-specific colors (terminal, code, gold glow, etc.)
 * that have no Material3 equivalent.
 *
 * Usage:
 *   val colors = LocalJarvisColors.current
 *   Box(Modifier.background(colors.terminalBackground))
 */
val LocalJarvisColors = staticCompositionLocalOf { DarkJarvisColors }

/**
 * Provides JARVIS-specific typography beyond the Material3 scale
 * (terminal body, code inline, file permissions, sensor value, etc.).
 *
 * Usage:
 *   val typo = LocalJarvisTypography.current
 *   Text(text = output, style = typo.terminalBody)
 */
val LocalJarvisTypography = staticCompositionLocalOf { DefaultJarvisExtraTypography }

/**
 * Tracks whether the app is currently in dark mode.
 * Useful for one-off color decisions without reading the full theme.
 */
val LocalIsDarkTheme = compositionLocalOf { true }

// ── Theme Composable ──────────────────────────────────────────────────────────

/**
 * JARVIS design system entry point.
 *
 * Wraps [MaterialTheme] with the JARVIS gold/obsidian color scheme, typography,
 * and shape scale. Also wires system bar colors to match the theme.
 *
 * @param darkTheme    True = obsidian dark (default), false = light.
 * @param content      The Composable tree to theme.
 */
@Composable
fun JarvisTheme(
    // JARVIS is always dark — it is a heads-up AI interface, not a document reader.
    // We never flip to the light scheme regardless of system preference.
    darkTheme: Boolean = true,
    content: @Composable () -> Unit,
) {
    val colorScheme = JarvisDarkColorScheme
    val jarvisColors = DarkJarvisColors

    // ── System bars ───────────────────────────────────────────────────────
    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            val window = (view.context as Activity).window
            // Edge-to-edge: let Compose draw under the status and nav bars
            WindowCompat.setDecorFitsSystemWindows(window, false)
            // Status bar — transparent, icons match theme
            window.statusBarColor = Color.Transparent.toArgb()
            WindowCompat.getInsetsController(window, view).apply {
                isAppearanceLightStatusBars     = !darkTheme
                isAppearanceLightNavigationBars = !darkTheme
            }
            // Navigation bar — transparent
            window.navigationBarColor = Color.Transparent.toArgb()
        }
    }

    CompositionLocalProvider(
        LocalJarvisColors      provides jarvisColors,
        LocalJarvisTypography  provides DefaultJarvisExtraTypography,
        LocalIsDarkTheme       provides darkTheme,
    ) {
        MaterialTheme(
            colorScheme = colorScheme,
            typography  = JarvisTypography,
            shapes      = JarvisShapes,
            content     = content,
        )
    }
}

// ── Convenience accessors ─────────────────────────────────────────────────────

/**
 * Shortcut accessors so Composables can write:
 *   JarvisTheme.colors.terminalBackground
 *   JarvisTheme.typography.terminalBody
 * instead of LocalJarvisColors.current.terminalBackground
 */
object JarvisTheme {
    val colors: JarvisColors
        @Composable @ReadOnlyComposable get() = LocalJarvisColors.current

    val typography: JarvisExtraTypography
        @Composable @ReadOnlyComposable get() = LocalJarvisTypography.current

    val isDark: Boolean
        @Composable @ReadOnlyComposable get() = LocalIsDarkTheme.current
}
