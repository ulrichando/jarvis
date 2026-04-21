package com.jarvis.android.presentation.chat

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlin.math.PI
import kotlin.math.sin

private val Accent      = Color(0xFF1E7FFF)
private val Overlay     = Color(0xFF000000)
private val TextPri     = Color(0xFFECECEC)
private val TextSec     = Color(0xFF8A8A8A)
private val MicBtnIdle  = Color(0xFF1E7FFF)
private val MicBtnLive  = Color(0xFFCF4A3C)

/**
 * Full-screen voice interaction overlay.
 *
 * Animates up from the bottom when the user taps the mic button on Home.
 * Shows the reactor visualisation centred at full size, a live transcription
 * (when available), and a prominent stop/send button.
 *
 * Architecture:
 * ```
 *   [close ✕]                     ← Top right: dismiss without sending
 *
 *   [ ArcReactor — full size ]    ← Same Three.js view as home, bigger here
 *
 *   ~~~~~~~~~~~~~~~~~~~~~~~~      ← Voice-bars (real amplitude when speaking)
 *
 *   "Scanning the network now…"   ← Live partial transcript (when recording)
 *
 *   [  Mic button 72dp  ]         ← Tap to stop / send to LLM
 *   Listening…
 * ```
 *
 * The overlay is driven entirely by state flowing through [ChatUiState] —
 * there is no local recording state. The hosting screen decides when to show
 * it (typically tied to [ChatUiState.isRecording]).
 */
@Composable
fun VoiceOverlay(
    isVisible:       Boolean,
    isRecording:     Boolean,
    audioLevel:      Float,
    transcript:      String,
    onToggleMic:     () -> Unit,
    onDismiss:       () -> Unit,
    modifier:        Modifier = Modifier,
) {
    AnimatedVisibility(
        visible = isVisible,
        enter   = slideInVertically(initialOffsetY = { it }) + fadeIn(),
        exit    = slideOutVertically(targetOffsetY = { it }) + fadeOut(),
        modifier = modifier,
    ) {
        VoiceOverlayContent(
            isRecording  = isRecording,
            audioLevel   = audioLevel,
            transcript   = transcript,
            onToggleMic  = onToggleMic,
            onDismiss    = onDismiss,
        )
    }
}

@Composable
private fun VoiceOverlayContent(
    isRecording: Boolean,
    audioLevel:  Float,
    transcript:  String,
    onToggleMic: () -> Unit,
    onDismiss:   () -> Unit,
) {
    val inf       = rememberInfiniteTransition(label = "voice-overlay")
    val waveTime by inf.animateFloat(
        initialValue  = 0f,
        targetValue   = (2.0 * PI * 4.0).toFloat(),
        animationSpec = infiniteRepeatable(tween(4_000, easing = LinearEasing)),
        label         = "waveTime",
    )
    val glowPulse by inf.animateFloat(
        initialValue  = 0.45f,
        targetValue   = 0.90f,
        animationSpec = infiniteRepeatable(
            tween(900, easing = FastOutSlowInEasing),
            RepeatMode.Reverse,
        ),
        label = "glow",
    )
    val amplitude by animateFloatAsState(
        targetValue   = if (isRecording) 1f else 0f,
        animationSpec = tween(500, easing = FastOutSlowInEasing),
        label         = "amp",
    )

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Overlay),
    ) {
        // ── Reactor ──────────────────────────────────────────────────────────
        // The same Three.js reactor used on home, rendered full-size here so
        // it reads as a single continuous presence rather than a tool that
        // suddenly appeared.
        ArcReactorWebView(
            isAiSpeaking = isRecording,
            audioLevel   = audioLevel,
            modifier     = Modifier.fillMaxSize(),
        )

        // ── Voice bars — centred under reactor ───────────────────────────────
        Canvas(modifier = Modifier.fillMaxSize()) {
            val numBars = 32
            val barW    = 5.dp.toPx()
            val gap     = 4.dp.toPx()
            val totalW  = numBars * barW + (numBars - 1) * gap
            val startX  = (size.width - totalW) / 2f
            val yCenter = size.height * 0.68f
            val maxH    = 72.dp.toPx()
            val minH    = 4.dp.toPx()
            val cornerR = barW / 2f

            for (i in 0 until numBars) {
                val t     = waveTime
                val phase = i * 0.38f
                // Layered sine waves for more organic motion.
                val wave  = sin(t * 1.8f + phase) * 0.55f +
                            sin(t * 3.1f + phase * 1.3f) * 0.45f
                val norm  = ((wave + 1f) / 2f).coerceIn(0f, 1f)
                // Real mic amplitude scales the height envelope.
                val mixed = norm * (0.45f + 0.55f * audioLevel)
                val barH  = (minH + (maxH - minH) * mixed) * amplitude

                drawRoundRect(
                    color        = Accent.copy(
                        alpha = (0.55f + 0.45f * mixed) * (0.3f + 0.7f * amplitude),
                    ),
                    topLeft      = Offset(startX + i * (barW + gap), yCenter - barH / 2f),
                    size         = Size(barW, barH),
                    cornerRadius = CornerRadius(cornerR),
                )
            }
        }

        // ── Bottom glow ──────────────────────────────────────────────────────
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(260.dp)
                .align(Alignment.BottomCenter)
                .background(
                    brush = Brush.verticalGradient(
                        colors = listOf(
                            Color.Transparent,
                            Accent.copy(alpha = glowPulse * amplitude * 0.78f),
                        ),
                    ),
                ),
        )

        // ── Top: close button ────────────────────────────────────────────────
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .statusBarsPadding()
                .padding(horizontal = 8.dp, vertical = 6.dp),
            horizontalArrangement = Arrangement.End,
        ) {
            IconButton(onClick = onDismiss) {
                Icon(
                    imageVector        = Icons.Default.Close,
                    contentDescription = "Close voice mode",
                    tint               = TextPri.copy(alpha = 0.85f),
                    modifier           = Modifier.size(24.dp),
                )
            }
        }

        // ── Bottom: transcript + big mic button ──────────────────────────────
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .align(Alignment.BottomCenter)
                .navigationBarsPadding()
                .padding(horizontal = 24.dp, vertical = 28.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            if (transcript.isNotBlank()) {
                Text(
                    text  = transcript,
                    style = MaterialTheme.typography.titleMedium.copy(
                        fontWeight = FontWeight.Medium,
                        fontSize   = 17.sp,
                    ),
                    color     = TextPri,
                    textAlign = TextAlign.Center,
                    maxLines  = 4,
                )
                Spacer(Modifier.height(20.dp))
            }

            // Big mic button — central affordance. Blue when idle, red while
            // actively recording so state is obvious even with no text.
            Box(
                modifier = Modifier
                    .size(76.dp)
                    .background(
                        color = if (isRecording) MicBtnLive else MicBtnIdle,
                        shape = CircleShape,
                    )
                    .border(
                        width = 2.dp,
                        color = Color.White.copy(alpha = 0.12f),
                        shape = CircleShape,
                    )
                    .clickable(onClick = onToggleMic),
                contentAlignment = Alignment.Center,
            ) {
                Icon(
                    imageVector        = if (isRecording) Icons.Default.Stop
                                         else Icons.Default.Mic,
                    contentDescription = if (isRecording) "Stop recording"
                                         else "Start recording",
                    tint               = Color.White,
                    modifier           = Modifier.size(32.dp),
                )
            }

            Spacer(Modifier.height(10.dp))

            Text(
                text  = when {
                    isRecording && transcript.isBlank() -> "Listening…"
                    isRecording                         -> "Tap to send"
                    else                                -> "Tap to speak"
                },
                style = MaterialTheme.typography.labelMedium.copy(fontSize = 13.sp),
                color = TextSec,
            )
        }
    }
}
