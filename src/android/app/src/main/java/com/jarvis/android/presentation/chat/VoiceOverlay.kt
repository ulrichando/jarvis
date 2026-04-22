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
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.MicOff
import androidx.compose.material.icons.filled.MoreHoriz
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.animation.core.Animatable
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
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
import com.jarvis.android.domain.model.Message
import com.jarvis.android.domain.model.MessageRole
import kotlin.math.PI
import kotlin.math.sin

private val Accent       = Color(0xFF1E7FFF)
private val Overlay      = Color(0xFF000000)
private val TextPri      = Color(0xFFECECEC)
private val TextSec      = Color(0xFFB0B0B0)
private val TextMuted    = Color(0xFF8A8A8A)
private val UserBubble   = Color(0xFF2C2C2E)
private val PillBg       = Color(0xFFF2F2F2)
private val PillFg       = Color(0xFF111111)
private val ControlIdle  = Color(0xFF2A2A2A)
private val ControlMuted = Color(0xFFCF4A3C)

/**
 * Full-screen voice interaction overlay — Claude-style.
 *
 * ```
 *   (subtle reactor background)
 *
 *   ┌─────────────────────────────────────┐
 *   │ assistant: lorem ipsum…             │  ← scrollable transcript
 *   │ ┌────────────────────────┐          │     of the live conversation
 *   │ │ user: text on dark bg  │          │     (latest at the bottom)
 *   │ └────────────────────────┘          │
 *   │ assistant: …streaming text          │
 *   └─────────────────────────────────────┘
 *
 *   [⚙]            [🎤]            [⋯ Stop]   ← gear / mute / stop pill
 * ```
 *
 * @param messages       Persisted history rows (user + assistant), oldest first.
 * @param streamingText  Token-streamed assistant text not yet persisted; rendered
 *                       as a virtual final row so the user sees the reply land
 *                       in real time without waiting for the DB insert.
 * @param isMuted        Whether the user's mic input is currently silenced.
 *                       The hosting screen flips this via [onToggleMute].
 * @param onToggleMute   Toggle mic on/off without leaving voice mode.
 * @param onOpenSettings Quick jump to Settings — same gear icon as the drawer.
 * @param onStop         Tap "Stop" → exit voice mode. Hosting screen handles
 *                       the actual cleanup (cancel STT, close overlay, drop TTS).
 */
@Composable
fun VoiceOverlay(
    isVisible:       Boolean,
    isRecording:     Boolean,
    audioLevel:      Float,
    messages:        List<Message>,
    streamingText:   String,
    liveTranscript:  String,
    isMuted:         Boolean,
    isAiSpeaking:    Boolean,
    speechTick:      Long,
    onToggleMute:    () -> Unit,
    onOpenSettings:  () -> Unit,
    onStop:          () -> Unit,
    modifier:        Modifier = Modifier,
) {
    AnimatedVisibility(
        visible = isVisible,
        enter   = slideInVertically(initialOffsetY = { it }) + fadeIn(),
        exit    = slideOutVertically(targetOffsetY = { it }) + fadeOut(),
        modifier = modifier,
    ) {
        VoiceOverlayContent(
            isRecording    = isRecording,
            audioLevel     = audioLevel,
            messages       = messages,
            streamingText  = streamingText,
            liveTranscript = liveTranscript,
            isMuted        = isMuted,
            isAiSpeaking   = isAiSpeaking,
            speechTick     = speechTick,
            onToggleMute   = onToggleMute,
            onOpenSettings = onOpenSettings,
            onStop         = onStop,
        )
    }
}

@Composable
private fun VoiceOverlayContent(
    isRecording:    Boolean,
    audioLevel:     Float,
    messages:       List<Message>,
    streamingText:  String,
    liveTranscript: String,
    isMuted:        Boolean,
    isAiSpeaking:   Boolean,
    speechTick:     Long,
    onToggleMute:   () -> Unit,
    onOpenSettings: () -> Unit,
    onStop:         () -> Unit,
) {
    val inf       = rememberInfiniteTransition(label = "voice-overlay")
    val waveTime by inf.animateFloat(
        initialValue  = 0f,
        targetValue   = (2.0 * PI * 4.0).toFloat(),
        animationSpec = infiniteRepeatable(tween(4_000, easing = LinearEasing)),
        label         = "waveTime",
    )
    // Fast baseline pulse — like a heartbeat under the AI's voice. Pulse rate
    // is the same whether streaming or just TTS-speaking; the *burst* below is
    // what carries the per-token rhythm.
    val glowPulse by inf.animateFloat(
        initialValue  = 0.40f,
        targetValue   = 0.95f,
        animationSpec = infiniteRepeatable(
            tween(700, easing = FastOutSlowInEasing),
            RepeatMode.Reverse,
        ),
        label = "glow",
    )
    val amplitude by animateFloatAsState(
        targetValue   = if (isRecording && !isMuted) 1f else 0f,
        animationSpec = tween(500, easing = FastOutSlowInEasing),
        label         = "amp",
    )

    // ── Word-driven intensity burst ─────────────────────────────────────
    // Two trigger sources, one shared Animatable:
    //   1. streamingText growth — bursts while text is still arriving
    //   2. speechTick — bursts on each WORD the TTS engine actually starts
    //      pronouncing, via UtteranceProgressListener.onRangeStart. This is
    //      the closest thing we have to real audio-amplitude data without
    //      Visualizer's restricted permission.
    val burst = remember { Animatable(0f) }
    LaunchedEffect(streamingText.length) {
        if (streamingText.isNotBlank()) {
            burst.snapTo(1f)
            burst.animateTo(0f, tween(350, easing = FastOutSlowInEasing))
        }
    }
    LaunchedEffect(speechTick) {
        // Bumps once per word being spoken — perfect for a glow heartbeat.
        if (speechTick > 0L) {
            burst.snapTo(0.9f)
            burst.animateTo(0f, tween(280, easing = FastOutSlowInEasing))
        }
    }

    val aiGlow by animateFloatAsState(
        targetValue   = if (isAiSpeaking) 1f else 0f,
        animationSpec = tween(400, easing = FastOutSlowInEasing),
        label         = "ai-glow",
    )

    Box(modifier = Modifier.fillMaxSize().background(Overlay)) {

        // ── AI-speaking glow — Claude-style pulsing accent at the bottom
        //     edge. Combines a slow baseline "breath" (glowPulse) with a
        //     fast per-token burst (burst.value) so the glow visibly pulses
        //     in time with what the model is saying. Brighter and taller
        //     when streaming OR TTS is active; off when neither.
        if (aiGlow > 0f) {
            val burstAlpha = 0.35f + 0.65f * (glowPulse * 0.55f + burst.value * 0.45f)
            val burstHeight = (160f + 60f * burst.value).dp
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(burstHeight)
                    .align(Alignment.BottomCenter)
                    .background(
                        brush = Brush.verticalGradient(
                            colors = listOf(
                                Color.Transparent,
                                Accent.copy(alpha = 0.18f * aiGlow * burstAlpha),
                                Accent.copy(alpha = 0.65f * aiGlow * burstAlpha),
                            ),
                        ),
                    ),
            )
        }

        // ── Scrollable conversation transcript ───────────────────────────────
        // Same shape the chat list uses but stripped of avatars / timestamps —
        // user turns get a gray bubble, assistant turns are plain text. The
        // streaming partial is rendered as a virtual last row so tokens flow
        // in live without waiting for the DB insert.
        val listState = rememberLazyListState()
        val total = messages.size + (if (streamingText.isNotBlank()) 1 else 0)

        LaunchedEffect(total, streamingText.length) {
            if (total > 0) listState.animateScrollToItem(total - 1)
        }

        LazyColumn(
            state               = listState,
            modifier            = Modifier
                .fillMaxSize()
                .statusBarsPadding()
                .padding(top = 56.dp, bottom = 120.dp),
            contentPadding      = PaddingValues(horizontal = 16.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            items(items = messages, key = { it.id }) { msg -> TranscriptRow(msg) }
            if (streamingText.isNotBlank()) {
                item(key = "streaming") {
                    Text(
                        text  = streamingText,
                        style = MaterialTheme.typography.bodyLarge.copy(
                            fontWeight = FontWeight.Normal,
                            fontSize   = 16.sp,
                        ),
                        color    = TextPri,
                        modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
                    )
                }
            }
        }

        // ── Live STT partial — italic gray text floating just above the
        //     bottom controls, exactly like Claude. Only appears while the
        //     user is actually speaking; auto-clears when STT finalises and
        //     the message is sent.
        if (liveTranscript.isNotBlank()) {
            Text(
                text  = liveTranscript,
                style = MaterialTheme.typography.bodyLarge.copy(
                    fontStyle = androidx.compose.ui.text.font.FontStyle.Italic,
                    fontSize  = 16.sp,
                ),
                color    = TextSec,
                modifier = Modifier
                    .fillMaxWidth()
                    .align(Alignment.BottomCenter)
                    .navigationBarsPadding()
                    .padding(start = 20.dp, end = 20.dp, bottom = 110.dp),
            )
        }

        // ── Bottom control row — Claude layout:
        //     gear alone on the left,  mic + Stop grouped on the right ──────
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .align(Alignment.BottomCenter)
                .navigationBarsPadding()
                .padding(start = 16.dp, end = 16.dp, top = 16.dp, bottom = 24.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            CircleControl(
                icon         = Icons.Default.Settings,
                contentDesc  = "Settings",
                bg           = ControlIdle,
                onClick      = onOpenSettings,
            )
            Spacer(Modifier.weight(1f))
            CircleControl(
                icon         = if (isMuted) Icons.Default.MicOff else Icons.Default.Mic,
                contentDesc  = if (isMuted) "Unmute microphone" else "Mute microphone",
                bg           = if (isMuted) ControlMuted else ControlIdle,
                onClick      = onToggleMute,
            )
            Spacer(Modifier.size(12.dp))
            StopPill(onClick = onStop, animating = isAiSpeaking)
        }
    }
}

/** A bubble (user) or plain block (assistant) showing a single conversation turn. */
@Composable
private fun TranscriptRow(msg: Message) {
    if (msg.role == MessageRole.USER) {
        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
            Text(
                text     = msg.content,
                style    = MaterialTheme.typography.bodyMedium.copy(fontSize = 15.sp),
                color    = TextPri,
                modifier = Modifier
                    .background(UserBubble, RoundedCornerShape(18.dp))
                    .padding(horizontal = 14.dp, vertical = 9.dp),
            )
        }
    } else {
        Text(
            text     = msg.content,
            style    = MaterialTheme.typography.bodyLarge.copy(fontSize = 16.sp),
            color    = TextPri,
            modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
        )
    }
}

/** Round 48dp button used by the gear and mic-mute slots. */
@Composable
private fun CircleControl(
    icon:        androidx.compose.ui.graphics.vector.ImageVector,
    contentDesc: String,
    bg:          Color,
    onClick:     () -> Unit,
) {
    Box(
        modifier = Modifier
            .size(48.dp)
            .background(bg, CircleShape)
            .border(1.dp, Color.White.copy(alpha = 0.10f), CircleShape)
            .clickable(onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Icon(
            imageVector        = icon,
            contentDescription = contentDesc,
            tint               = Color.White,
            modifier           = Modifier.size(22.dp),
        )
    }
}

/**
 * "••• Stop" pill — Claude's primary CTA in voice mode. The three dots
 * bounce in sequence while the AI is talking (Claude does the same thing),
 * giving the user a clear "I'm responding" cue. When idle the dots are
 * static.
 */
@Composable
private fun StopPill(onClick: () -> Unit, animating: Boolean) {
    Row(
        modifier = Modifier
            .background(PillBg, CircleShape)
            .clickable(onClick = onClick)
            .padding(horizontal = 18.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        BouncingDots(animating = animating)
        Spacer(Modifier.size(8.dp))
        Text(
            text  = "Stop",
            style = MaterialTheme.typography.labelLarge.copy(
                fontWeight = FontWeight.SemiBold,
                fontSize   = 14.sp,
            ),
            color = PillFg,
        )
    }
}

/** Three dots that bounce in a wave while [animating]; flat when not. */
@Composable
private fun BouncingDots(animating: Boolean) {
    val transition = rememberInfiniteTransition(label = "dots")
    val phase by transition.animateFloat(
        initialValue  = 0f,
        targetValue   = (2f * PI).toFloat(),
        animationSpec = infiniteRepeatable(tween(900, easing = LinearEasing)),
        label         = "dots-phase",
    )
    Row(verticalAlignment = Alignment.CenterVertically) {
        repeat(3) { i ->
            val offsetY: Float = if (animating) {
                // Per-dot phase shift so they wave left-to-right.
                val o = sin(phase + i * 0.7f) * 3f
                o.coerceIn(-3f, 3f)
            } else 0f
            Box(
                modifier = Modifier
                    .size(5.dp)
                    .offset(y = offsetY.dp)
                    .background(PillFg, CircleShape),
            )
            if (i < 2) Spacer(Modifier.size(3.dp))
        }
    }
}
