package com.jarvis.android.core.designsystem

import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.ui.graphics.Color

// ── Raw palette ───────────────────────────────────────────────────────────────

/** JARVIS gold/obsidian palette — use via [JarvisColors], not directly in Composables. */
object JarvisPalette {

    // ── Obsidian ─────────────────────────────────────────────────────────────
    val ObsidianBlack    = Color(0xFF0A0A0A)
    val SurfaceDark      = Color(0xFF141414)
    val SurfaceElevated  = Color(0xFF1E1E1E)
    val SurfaceOverlay   = Color(0xFF242424)

    // ── Gold ─────────────────────────────────────────────────────────────────
    val GoldPrimary      = Color(0xFFC9A84C)
    val GoldGlow         = Color(0xFFF5D680)
    val GoldMuted        = Color(0xFF7A6530)
    val GoldBorder       = Color(0xFF3D2E0F)
    val GoldDim          = Color(0xFF4A3A1E)

    // ── Semantic ─────────────────────────────────────────────────────────────
    val UserBubble       = Color(0xFF1C2E4A)
    val UserBubbleLight  = Color(0xFFDCEAFB)
    val ErrorRed         = Color(0xFFCF4A3C)
    val ErrorContainer   = Color(0xFF4A1510)
    val SuccessGreen     = Color(0xFF3CAF6E)
    val WarningAmber     = Color(0xFFE0A030)

    // ── Text ─────────────────────────────────────────────────────────────────
    val TextPrimary      = Color(0xFFF0EDE8)
    val TextSecondary    = Color(0xFF8A8070)
    val TextDisabled     = Color(0xFF4A4540)
    val TextOnGold       = Color(0xFF0A0A0A)

    // ── Terminal ─────────────────────────────────────────────────────────────
    val TerminalBg       = Color(0xFF050505)
    val TerminalText     = Color(0xFFC8FFB4)   // classic terminal green
    val TerminalCursor   = Color(0xFFC9A84C)   // gold cursor
    val TerminalDim      = Color(0xFF5A7A50)

    // ── Code ─────────────────────────────────────────────────────────────────
    val CodeBg           = Color(0xFF0D0D0D)
    val CodeBorder       = Color(0xFF2A2A2A)
    val CodeBar          = Color(0xFF1A1A1A)

    // ── Light mode ───────────────────────────────────────────────────────────
    val LightBackground  = Color(0xFFF5F2EE)
    val LightSurface     = Color(0xFFFFFFFF)
    val LightSurface2    = Color(0xFFF0EDE8)
    val GoldPrimaryLight = Color(0xFFB8922A)
    val GoldMutedLight   = Color(0xFFD4A94A)
    val TextPrimaryLight = Color(0xFF1A1714)
    val TextSecondaryLight = Color(0xFF6B6055)
}

// ── JARVIS blue palette additions ─────────────────────────────────────────────
// The voice / home screen uses #1E7FFF as the AI accent colour. This is now the
// Material3 primary so that every screen (buttons, progress bars, chips, icons)
// is consistent with the home screen instead of fighting against it.

private val JarvisBlue        = Color(0xFF1E7FFF)   // primary accent
private val JarvisBlueDark    = Color(0xFF0A2A5A)   // primaryContainer
private val JarvisBlueLight   = Color(0xFFBDD5FF)   // onPrimaryContainer
private val JarvisBlueInverse = Color(0xFF1060C0)   // inversePrimary

// ── Material3 dark color scheme ───────────────────────────────────────────────

internal val JarvisDarkColorScheme = darkColorScheme(
    // ── Primary (JARVIS blue — matches voice screen AI accent) ───────────────
    primary                = JarvisBlue,
    onPrimary              = Color(0xFFFFFFFF),
    primaryContainer       = JarvisBlueDark,
    onPrimaryContainer     = JarvisBlueLight,
    inversePrimary         = JarvisBlueInverse,

    // ── Secondary (gold — retained for brand typography / icon accents) ──────
    secondary              = JarvisPalette.GoldPrimary,
    onSecondary            = JarvisPalette.TextOnGold,
    secondaryContainer     = JarvisPalette.GoldDim,
    onSecondaryContainer   = JarvisPalette.GoldGlow,

    // ── Tertiary (user bubble blue) ───────────────────────────────────────────
    tertiary               = JarvisPalette.UserBubble,
    onTertiary             = JarvisPalette.TextPrimary,
    tertiaryContainer      = Color(0xFF0F1E30),
    onTertiaryContainer    = Color(0xFFBDD5F0),

    // ── Background / Surface ─────────────────────────────────────────────────
    background             = JarvisPalette.ObsidianBlack,
    onBackground           = JarvisPalette.TextPrimary,
    surface                = JarvisPalette.SurfaceDark,
    onSurface              = JarvisPalette.TextPrimary,
    surfaceVariant         = JarvisPalette.SurfaceElevated,
    onSurfaceVariant       = JarvisPalette.TextSecondary,
    surfaceTint            = JarvisBlue,
    inverseSurface         = JarvisPalette.TextPrimary,
    inverseOnSurface       = JarvisPalette.ObsidianBlack,

    // ── Outline ───────────────────────────────────────────────────────────────
    outline                = Color(0xFF2A3A4A),    // dark blue-tinted border
    outlineVariant         = Color(0xFF1A2230),

    // ── Error ─────────────────────────────────────────────────────────────────
    error                  = JarvisPalette.ErrorRed,
    onError                = JarvisPalette.TextPrimary,
    errorContainer         = JarvisPalette.ErrorContainer,
    onErrorContainer       = Color(0xFFFFB4AB),

    // ── Scrim ─────────────────────────────────────────────────────────────────
    scrim                  = Color(0xCC000000),
)

// ── Material3 light color scheme ──────────────────────────────────────────────

internal val JarvisLightColorScheme = lightColorScheme(
    primary                = JarvisPalette.GoldPrimaryLight,
    onPrimary              = JarvisPalette.LightBackground,
    primaryContainer       = Color(0xFFFFE5A0),
    onPrimaryContainer     = Color(0xFF3A2800),
    inversePrimary         = JarvisPalette.GoldPrimary,

    secondary              = JarvisPalette.GoldMutedLight,
    onSecondary            = JarvisPalette.LightBackground,
    secondaryContainer     = Color(0xFFFFEDD0),
    onSecondaryContainer   = Color(0xFF2A1800),

    tertiary               = Color(0xFF2A5080),
    onTertiary             = Color(0xFFFFFFFF),
    tertiaryContainer      = JarvisPalette.UserBubbleLight,
    onTertiaryContainer    = Color(0xFF001D36),

    background             = JarvisPalette.LightBackground,
    onBackground           = JarvisPalette.TextPrimaryLight,
    surface                = JarvisPalette.LightSurface,
    onSurface              = JarvisPalette.TextPrimaryLight,
    surfaceVariant         = JarvisPalette.LightSurface2,
    onSurfaceVariant       = JarvisPalette.TextSecondaryLight,
    inverseSurface         = Color(0xFF1A1714),
    inverseOnSurface       = JarvisPalette.LightBackground,

    outline                = Color(0xFFB0A090),
    outlineVariant         = Color(0xFFD8D0C8),

    error                  = Color(0xFFB3261E),
    onError                = Color(0xFFFFFFFF),
    errorContainer         = Color(0xFFF9DEDC),
    onErrorContainer       = Color(0xFF410E0B),

    scrim                  = Color(0x80000000),
)

// ── Extended JARVIS-specific colors (not in Material3 roles) ─────────────────

/**
 * Custom JARVIS colors that have no Material3 equivalent.
 * Accessed via [LocalJarvisColors] composition local inside any Composable.
 */
data class JarvisColors(
    val terminalBackground: Color,
    val terminalText: Color,
    val terminalCursor: Color,
    val terminalDim: Color,
    val codeBg: Color,
    val codeBorder: Color,
    val codeBar: Color,
    val userBubble: Color,
    val goldGlow: Color,
    val goldBorder: Color,
    val successGreen: Color,
    val warningAmber: Color,
    val textDisabled: Color,
    val surfaceOverlay: Color,
)

internal val DarkJarvisColors = JarvisColors(
    terminalBackground = JarvisPalette.TerminalBg,
    terminalText       = JarvisPalette.TerminalText,
    terminalCursor     = JarvisPalette.TerminalCursor,
    terminalDim        = JarvisPalette.TerminalDim,
    codeBg             = JarvisPalette.CodeBg,
    codeBorder         = JarvisPalette.CodeBorder,
    codeBar            = JarvisPalette.CodeBar,
    userBubble         = JarvisPalette.UserBubble,
    goldGlow           = JarvisPalette.GoldGlow,
    goldBorder         = JarvisPalette.GoldBorder,
    successGreen       = JarvisPalette.SuccessGreen,
    warningAmber       = JarvisPalette.WarningAmber,
    textDisabled       = JarvisPalette.TextDisabled,
    surfaceOverlay     = JarvisPalette.SurfaceOverlay,
)

internal val LightJarvisColors = JarvisColors(
    terminalBackground = Color(0xFF1A1A1A),
    terminalText       = JarvisPalette.TerminalText,
    terminalCursor     = JarvisPalette.GoldPrimaryLight,
    terminalDim        = JarvisPalette.TerminalDim,
    codeBg             = Color(0xFFF0EDE8),
    codeBorder         = Color(0xFFD8D0C8),
    codeBar            = Color(0xFFE8E4DE),
    userBubble         = JarvisPalette.UserBubbleLight,
    goldGlow           = JarvisPalette.GoldPrimaryLight,
    goldBorder         = Color(0xFFD4A94A),
    successGreen       = Color(0xFF2E8A55),
    warningAmber       = Color(0xFFC08020),
    textDisabled       = Color(0xFFB0A898),
    surfaceOverlay     = Color(0xFFEAE7E2),
)
