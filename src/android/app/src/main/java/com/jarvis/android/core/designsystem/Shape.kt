package com.jarvis.android.core.designsystem

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Shapes
import androidx.compose.ui.unit.dp

/**
 * JARVIS shape system.
 *
 * Material3 Shapes scale is used for standard components (cards, dialogs, chips).
 * [JarvisShapes] extends this with component-specific shapes not in M3 scale.
 */
val JarvisShapes = Shapes(
    // Extra small — chips, badges, small tags
    extraSmall = RoundedCornerShape(4.dp),
    // Small — text fields, snackbars, small buttons
    small      = RoundedCornerShape(8.dp),
    // Medium — cards, drawers, bottom sheets
    medium     = RoundedCornerShape(12.dp),
    // Large — dialogs, modals, large cards
    large      = RoundedCornerShape(16.dp),
    // Extra large — full-screen bottom sheets, panels
    extraLarge = RoundedCornerShape(28.dp),
)

// ── Component-specific shapes (accessed directly, not via MaterialTheme.shapes) ─

object JarvisComponentShapes {

    /** Chat message bubble — 18dp all corners, flat on sender's side corner */
    val MessageBubbleUser = RoundedCornerShape(
        topStart     = 18.dp,
        topEnd       = 4.dp,   // flat top-right (sender side)
        bottomEnd    = 18.dp,
        bottomStart  = 18.dp,
    )

    val MessageBubbleAssistant = RoundedCornerShape(
        topStart     = 4.dp,   // flat top-left (receiver side)
        topEnd       = 18.dp,
        bottomEnd    = 18.dp,
        bottomStart  = 18.dp,
    )

    /** Input bar — pill shape */
    val InputBar = RoundedCornerShape(28.dp)

    /** Send button — circle */
    val SendButton = RoundedCornerShape(50)

    /** Code block container */
    val CodeBlock = RoundedCornerShape(8.dp)

    /** Terminal tab */
    val TerminalTab = RoundedCornerShape(topStart = 8.dp, topEnd = 8.dp)

    /** Sensor card */
    val SensorCard = RoundedCornerShape(12.dp)

    /** Bottom nav bar background */
    val BottomNav = RoundedCornerShape(topStart = 16.dp, topEnd = 16.dp)

    /** Notification / permission pill badge */
    val Badge = RoundedCornerShape(50)

    /** File manager breadcrumb segment */
    val BreadcrumbSegment = RoundedCornerShape(6.dp)

    /** Process manager row — subtle rounding */
    val ProcessRow = RoundedCornerShape(8.dp)

    /** Quick command chip in terminal */
    val QuickCommandChip = RoundedCornerShape(20.dp)

    /** Gold glow indicator dot (avatar-style) */
    val AvatarDot = RoundedCornerShape(50)
}
