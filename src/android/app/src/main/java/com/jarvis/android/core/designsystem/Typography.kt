package com.jarvis.android.core.designsystem

import androidx.compose.material3.Typography
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.unit.sp

// ── Font families ─────────────────────────────────────────────────────────────
// Using system fonts for emulator compatibility.
// Replace with downloadable Google Fonts on production builds (requires GMS).

/** Space Grotesk → system sans-serif for display/heading roles. */
val SpaceGroteskFamily = FontFamily.SansSerif

/** DM Sans → system sans-serif for body/label roles. */
val DmSansFamily = FontFamily.SansSerif

/** JetBrains Mono → system monospace for terminal/code roles. */
val JetBrainsMonoFamily = FontFamily.Monospace

// ── Material3 Typography ──────────────────────────────────────────────────────

/**
 * JARVIS typography scale mapped to Material3 type roles.
 *
 * Display/Headline → Space Grotesk (bold, screen-level headings)
 * Title/Body/Label → DM Sans (readable, clean UI text)
 * Mono styles (not in M3 scale) → accessed via [JarvisTypography]
 */
val JarvisTypography = Typography(

    // ── Display ───────────────────────────────────────────────────────────
    displayLarge = TextStyle(
        fontFamily = SpaceGroteskFamily,
        fontWeight = FontWeight.Bold,
        fontSize   = 57.sp,
        lineHeight = 64.sp,
        letterSpacing = (-0.25).sp,
    ),
    displayMedium = TextStyle(
        fontFamily = SpaceGroteskFamily,
        fontWeight = FontWeight.Bold,
        fontSize   = 45.sp,
        lineHeight = 52.sp,
        letterSpacing = 0.sp,
    ),
    displaySmall = TextStyle(
        fontFamily = SpaceGroteskFamily,
        fontWeight = FontWeight.SemiBold,
        fontSize   = 36.sp,
        lineHeight = 44.sp,
        letterSpacing = 0.sp,
    ),

    // ── Headline ──────────────────────────────────────────────────────────
    headlineLarge = TextStyle(
        fontFamily = SpaceGroteskFamily,
        fontWeight = FontWeight.Bold,
        fontSize   = 32.sp,
        lineHeight = 40.sp,
        letterSpacing = 0.sp,
    ),
    headlineMedium = TextStyle(
        fontFamily = SpaceGroteskFamily,
        fontWeight = FontWeight.SemiBold,
        fontSize   = 28.sp,
        lineHeight = 36.sp,
        letterSpacing = 0.sp,
    ),
    headlineSmall = TextStyle(
        fontFamily = SpaceGroteskFamily,
        fontWeight = FontWeight.SemiBold,
        fontSize   = 24.sp,
        lineHeight = 32.sp,
        letterSpacing = 0.sp,
    ),

    // ── Title ─────────────────────────────────────────────────────────────
    titleLarge = TextStyle(
        fontFamily = SpaceGroteskFamily,
        fontWeight = FontWeight.SemiBold,
        fontSize   = 22.sp,
        lineHeight = 28.sp,
        letterSpacing = 0.sp,
    ),
    titleMedium = TextStyle(
        fontFamily = DmSansFamily,
        fontWeight = FontWeight.SemiBold,
        fontSize   = 16.sp,
        lineHeight = 24.sp,
        letterSpacing = 0.15.sp,
    ),
    titleSmall = TextStyle(
        fontFamily = DmSansFamily,
        fontWeight = FontWeight.Medium,
        fontSize   = 14.sp,
        lineHeight = 20.sp,
        letterSpacing = 0.1.sp,
    ),

    // ── Body ──────────────────────────────────────────────────────────────
    bodyLarge = TextStyle(
        fontFamily = DmSansFamily,
        fontWeight = FontWeight.Normal,
        fontSize   = 16.sp,
        lineHeight = 24.sp,
        letterSpacing = 0.5.sp,
    ),
    bodyMedium = TextStyle(
        fontFamily = DmSansFamily,
        fontWeight = FontWeight.Normal,
        fontSize   = 15.sp,       // primary message text size
        lineHeight = 22.sp,
        letterSpacing = 0.25.sp,
    ),
    bodySmall = TextStyle(
        fontFamily = DmSansFamily,
        fontWeight = FontWeight.Normal,
        fontSize   = 12.sp,
        lineHeight = 16.sp,
        letterSpacing = 0.4.sp,
    ),

    // ── Label ─────────────────────────────────────────────────────────────
    labelLarge = TextStyle(
        fontFamily = DmSansFamily,
        fontWeight = FontWeight.Medium,
        fontSize   = 14.sp,
        lineHeight = 20.sp,
        letterSpacing = 0.1.sp,
    ),
    labelMedium = TextStyle(
        fontFamily = DmSansFamily,
        fontWeight = FontWeight.Medium,
        fontSize   = 12.sp,
        lineHeight = 16.sp,
        letterSpacing = 0.5.sp,
    ),
    labelSmall = TextStyle(
        fontFamily = DmSansFamily,
        fontWeight = FontWeight.Medium,
        fontSize   = 11.sp,
        lineHeight = 16.sp,
        letterSpacing = 0.5.sp,
    ),
)

// ── Extra type styles (accessed via LocalJarvisTypography) ────────────────────

/**
 * JARVIS-specific text styles beyond the Material3 scale.
 * Accessed via [LocalJarvisTypography] inside Composables:
 *   val typo = LocalJarvisTypography.current
 *   Text(style = typo.terminalBody, ...)
 */
data class JarvisExtraTypography(
    /** Terminal output — JetBrains Mono, 13sp */
    val terminalBody: TextStyle,
    /** Terminal bold (bright colors, bold sequences) */
    val terminalBold: TextStyle,
    /** Inline code span inside chat messages */
    val codeInline: TextStyle,
    /** Code block language label ("kotlin", "bash", etc.) */
    val codeLabel: TextStyle,
    /** Conversation list timestamp */
    val timestamp: TextStyle,
    /** System property key in the props viewer */
    val propKey: TextStyle,
    /** System property value */
    val propValue: TextStyle,
    /** Process name in process manager */
    val processName: TextStyle,
    /** File name in file manager */
    val fileName: TextStyle,
    /** File permissions (rwxr-xr-x) */
    val filePerms: TextStyle,
    /** Sensor reading value (large, chart label) */
    val sensorValue: TextStyle,
    /** Navigation tab label */
    val navLabel: TextStyle,
)

val DefaultJarvisExtraTypography = JarvisExtraTypography(
    terminalBody = TextStyle(
        fontFamily    = JetBrainsMonoFamily,
        fontWeight    = FontWeight.Normal,
        fontSize      = 13.sp,
        lineHeight    = 18.sp,
        letterSpacing = 0.sp,
    ),
    terminalBold = TextStyle(
        fontFamily    = JetBrainsMonoFamily,
        fontWeight    = FontWeight.Bold,
        fontSize      = 13.sp,
        lineHeight    = 18.sp,
        letterSpacing = 0.sp,
    ),
    codeInline = TextStyle(
        fontFamily    = JetBrainsMonoFamily,
        fontWeight    = FontWeight.Normal,
        fontSize      = 13.sp,
        lineHeight    = 20.sp,
        letterSpacing = 0.sp,
    ),
    codeLabel = TextStyle(
        fontFamily    = JetBrainsMonoFamily,
        fontWeight    = FontWeight.Normal,
        fontSize      = 11.sp,
        lineHeight    = 16.sp,
        letterSpacing = 0.5.sp,
    ),
    timestamp = TextStyle(
        fontFamily    = DmSansFamily,
        fontWeight    = FontWeight.Normal,
        fontSize      = 11.sp,
        lineHeight    = 14.sp,
        letterSpacing = 0.4.sp,
    ),
    propKey = TextStyle(
        fontFamily    = JetBrainsMonoFamily,
        fontWeight    = FontWeight.Normal,
        fontSize      = 12.sp,
        lineHeight    = 16.sp,
        letterSpacing = 0.sp,
    ),
    propValue = TextStyle(
        fontFamily    = JetBrainsMonoFamily,
        fontWeight    = FontWeight.Normal,
        fontSize      = 12.sp,
        lineHeight    = 16.sp,
        letterSpacing = 0.sp,
    ),
    processName = TextStyle(
        fontFamily    = DmSansFamily,
        fontWeight    = FontWeight.Medium,
        fontSize      = 13.sp,
        lineHeight    = 18.sp,
        letterSpacing = 0.sp,
    ),
    fileName = TextStyle(
        fontFamily    = DmSansFamily,
        fontWeight    = FontWeight.Normal,
        fontSize      = 14.sp,
        lineHeight    = 20.sp,
        letterSpacing = 0.sp,
    ),
    filePerms = TextStyle(
        fontFamily    = JetBrainsMonoFamily,
        fontWeight    = FontWeight.Normal,
        fontSize      = 11.sp,
        lineHeight    = 16.sp,
        letterSpacing = 0.sp,
    ),
    sensorValue = TextStyle(
        fontFamily    = SpaceGroteskFamily,
        fontWeight    = FontWeight.Bold,
        fontSize      = 28.sp,
        lineHeight    = 34.sp,
        letterSpacing = (-0.5).sp,
    ),
    navLabel = TextStyle(
        fontFamily    = DmSansFamily,
        fontWeight    = FontWeight.Medium,
        fontSize      = 11.sp,
        lineHeight    = 14.sp,
        letterSpacing = 0.sp,
    ),
)
