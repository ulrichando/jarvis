package com.jarvis.android.presentation.components

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.unit.dp

/**
 * A blinking block cursor shown at the tail of a streaming assistant message.
 * Fades in/out at ~1 Hz. Hidden automatically when [visible] is false.
 */
@Composable
fun StreamingCursor(
    modifier: Modifier = Modifier,
    visible: Boolean = true,
) {
    if (!visible) return

    val transition = rememberInfiniteTransition(label = "cursor_blink")
    val alpha by transition.animateFloat(
        initialValue   = 1f,
        targetValue    = 0f,
        animationSpec  = infiniteRepeatable(
            animation  = tween(durationMillis = 500),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "cursor_alpha",
    )

    Box(
        modifier = modifier
            .width(8.dp)
            .height(16.dp)
            .alpha(alpha)
            .background(MaterialTheme.colorScheme.primary),
    )
}
