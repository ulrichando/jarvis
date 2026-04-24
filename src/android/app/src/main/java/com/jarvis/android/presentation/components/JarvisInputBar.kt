package com.jarvis.android.presentation.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.GraphicEq
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

// ── Design tokens ─────────────────────────────────────────────────────────────
//
// Kept deliberately in sync with ChatScreen / HomeHero / VoiceOverlay so the
// input bar reads as a direct continuation of the screen, not a grafted card.

private val ScreenBg   = Color(0xFF0A0A0A)
private val PillBg     = Color(0xFF1F1F1F)   // lifted one step above the screen
private val PillBorder = Color(0x12FFFFFF)   // 7 % white — barely visible edge
private val TextPri    = Color(0xFFECECEC)
private val TextHint   = Color(0xFF7A7A7A)
private val TextMuted  = Color(0xFF8A8A8A)
private val Accent     = Color(0xFF1E7FFF)
private val VoiceFill  = Color(0xFFECECEC)   // near-white fill for the voice button
private val IconGhost  = Color(0xFFB0B0B0)

/**
 * JARVIS chat input bar — minimalist pill inspired by the Claude mobile app.
 *
 * Layout:
 * ```
 *   ┌───────────────────────────────────────────┐
 *   │  Chat with JARVIS...                      │
 *   │                                           │
 *   │  [ + ]                    [🎤]   [ ⬤ ]   │
 *   └───────────────────────────────────────────┘
 * ```
 *
 * - `+` on the left is reserved for attachments (wired via [onAttach]).
 * - The mic icon on the right triggers quick STT → text.
 * - The filled white circle on the far right opens the **voice overlay** for
 *   full-screen voice interaction (hands-free mode).
 * - When the user has typed text, the voice button morphs into a Send button
 *   so the primary CTA stays in the same physical spot.
 * - While the AI is streaming, it becomes a Stop button.
 *
 * @param text          Current input field content.
 * @param onTextChange  Called on every keystroke.
 * @param onSend        Called when the user taps the Send button.
 * @param onStop        Called when the user taps Stop during streaming.
 * @param onVoice       Tap-to-start dictation (fills the text field).
 * @param onVoiceMode   Open the full-screen voice overlay — the "phone call with
 *                      JARVIS" affordance. When null, the voice-mode button is
 *                      hidden and Send takes its place on the right.
 * @param isStreaming   True while a response turn is in flight.
 * @param isRecording   True while STT is actively listening (drives mic tint).
 * @param enabled       False disables all interactions.
 */
@Composable
fun JarvisInputBar(
    text:           String,
    onTextChange:   (String) -> Unit,
    onSend:         () -> Unit,
    onStop:         () -> Unit,
    modifier:       Modifier = Modifier,
    onAttach:       (() -> Unit)? = null,
    isStreaming:    Boolean = false,
    enabled:        Boolean = true,
    placeholder:    String  = "Chat with JARVIS…",
    onVoice:        (() -> Unit)? = null,
    onVoiceMode:    (() -> Unit)? = null,
    isRecording:    Boolean = false,
    /** `file://` URI of a staged image preview. Non-null → render the chip. */
    pendingImagePreviewUri: String? = null,
    /** Display name of a staged text file. Non-null → render the file chip. */
    pendingFileName:        String? = null,
    /** Tap on the chip's × — clears the staged attachment. */
    onRemoveAttachment:     (() -> Unit)? = null,
    // The following params are retained for source compatibility with the old
    // two-row design but intentionally unused in this minimalist layout — TTS
    // toggle + routing now live elsewhere (top bar + long-press).
    @Suppress("UNUSED_PARAMETER") ttsEnabled:     Boolean  = true,
    @Suppress("UNUSED_PARAMETER") onToggleTts:    (() -> Unit)? = null,
    @Suppress("UNUSED_PARAMETER") routingLabel:   String   = "Auto",
    @Suppress("UNUSED_PARAMETER") onCycleRouting: (() -> Unit)? = null,
) {
    val hasText = text.isNotBlank()

    Box(
        modifier = modifier
            .fillMaxWidth()
            .background(ScreenBg),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .padding(start = 12.dp, end = 12.dp, top = 4.dp, bottom = 8.dp),
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(PillBg, RoundedCornerShape(28.dp))
                    .border(1.dp, PillBorder, RoundedCornerShape(28.dp))
                    .padding(horizontal = 18.dp, vertical = 12.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                // ── Row 0 (optional): staged attachment chip ─────────────
                if (pendingImagePreviewUri != null || pendingFileName != null) {
                    AttachmentChip(
                        imageUri  = pendingImagePreviewUri,
                        fileName  = pendingFileName,
                        onRemove  = onRemoveAttachment ?: {},
                    )
                }

                // ── Row 1: the text field ────────────────────────────────
                BasicTextField(
                    value         = text,
                    onValueChange = onTextChange,
                    enabled       = enabled && !isStreaming,
                    textStyle     = MaterialTheme.typography.bodyLarge.copy(
                        color    = TextPri,
                        fontSize = 16.sp,
                    ),
                    cursorBrush   = SolidColor(Accent),
                    keyboardOptions = KeyboardOptions(
                        capitalization = KeyboardCapitalization.Sentences,
                        imeAction      = ImeAction.Default,
                    ),
                    maxLines = 6,
                    modifier = Modifier
                        .fillMaxWidth()
                        .heightIn(min = 28.dp, max = 140.dp),
                    decorationBox = { inner ->
                        Box(contentAlignment = Alignment.CenterStart) {
                            if (text.isEmpty()) {
                                Text(
                                    text  = placeholder,
                                    style = MaterialTheme.typography.bodyLarge.copy(
                                        fontSize = 16.sp,
                                    ),
                                    color = TextHint,
                                )
                            }
                            inner()
                        }
                    },
                )

                // ── Row 2: actions ───────────────────────────────────────
                Row(
                    modifier          = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    // Attach / plus — left
                    IconPillButton(
                        icon             = Icons.Default.Add,
                        contentDesc      = "Attach",
                        tint             = IconGhost,
                        enabled          = enabled && onAttach != null,
                        onClick          = { onAttach?.invoke() },
                    )

                    Spacer(Modifier.weight(1f))

                    // Dictation mic — tap to talk, transcript appears in the
                    // input field. Hidden while the user is composing text so
                    // the Send button stands alone on the right. The actual
                    // mic permission is requested at tap-time (not at app
                    // launch) — see ChatScreen's onVoice handler.
                    if (onVoice != null && !isStreaming && !hasText) {
                        IconPillButton(
                            icon        = if (isRecording) Icons.Default.Stop else Icons.Default.Mic,
                            contentDesc = if (isRecording) "Stop recording" else "Dictate",
                            tint        = if (isRecording) Accent else IconGhost,
                            enabled     = enabled,
                            onClick     = onVoice,
                        )
                        Spacer(Modifier.width(6.dp))
                    }

                    // Primary CTA — filled white circle. Morphs through:
                    //   no text + voice-mode available → voice mode (waveform)
                    //   has text                       → send
                    //   streaming                      → stop
                    val primaryAction: () -> Unit
                    val primaryEnabled: Boolean
                    val primaryIcon: androidx.compose.ui.graphics.vector.ImageVector
                    val primaryDesc: String
                    val primaryTint: Color
                    val primaryBg:   Color

                    when {
                        isStreaming -> {
                            primaryAction  = onStop
                            primaryEnabled = true
                            primaryIcon    = Icons.Default.Stop
                            primaryDesc    = "Stop"
                            primaryTint    = MaterialTheme.colorScheme.error
                            primaryBg      = MaterialTheme.colorScheme.error.copy(alpha = 0.15f)
                        }
                        hasText -> {
                            primaryAction  = onSend
                            primaryEnabled = enabled
                            primaryIcon    = Icons.AutoMirrored.Filled.Send
                            primaryDesc    = "Send"
                            primaryTint    = Color(0xFF0A0A0A)
                            primaryBg      = VoiceFill
                        }
                        onVoiceMode != null -> {
                            primaryAction  = onVoiceMode
                            primaryEnabled = enabled
                            primaryIcon    = Icons.Default.GraphicEq
                            primaryDesc    = "Voice mode"
                            primaryTint    = Color(0xFF0A0A0A)
                            primaryBg      = VoiceFill
                        }
                        else -> {
                            primaryAction  = onSend
                            primaryEnabled = false
                            primaryIcon    = Icons.AutoMirrored.Filled.Send
                            primaryDesc    = "Send"
                            primaryTint    = Color.White.copy(alpha = 0.35f)
                            primaryBg      = Color(0x14FFFFFF)
                        }
                    }

                    Box(
                        contentAlignment = Alignment.Center,
                        modifier = Modifier
                            .size(40.dp)
                            .background(color = primaryBg, shape = CircleShape)
                            .clickable(
                                enabled = primaryEnabled,
                                role    = Role.Button,
                                onClick = primaryAction,
                            ),
                    ) {
                        Icon(
                            imageVector        = primaryIcon,
                            contentDescription = primaryDesc,
                            tint               = primaryTint,
                            modifier           = Modifier.size(20.dp),
                        )
                    }
                }
            }
        }
    }
}

/**
 * Compact chip shown above the text field when an attachment is staged —
 * matches Claude's "you picked an image, here it is, type something and
 * send" affordance. Clicking × calls [onRemove].
 */
@Composable
private fun AttachmentChip(
    imageUri: String?,
    fileName: String?,
    onRemove: () -> Unit,
) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier
            .background(Color(0x20FFFFFF), RoundedCornerShape(12.dp))
            .padding(start = 6.dp, top = 6.dp, bottom = 6.dp, end = 4.dp),
    ) {
        if (imageUri != null) {
            // Thumbnail — Coil handles file:// URIs natively.
            coil3.compose.AsyncImage(
                model              = imageUri,
                contentDescription = "Attached image",
                contentScale       = androidx.compose.ui.layout.ContentScale.Crop,
                modifier           = Modifier
                    .size(40.dp)
                    .background(Color(0xFF101010), RoundedCornerShape(8.dp)),
            )
            Spacer(Modifier.width(10.dp))
            Text(
                text  = "Image attached",
                color = TextPri,
                fontSize = 13.sp,
            )
        } else {
            Icon(
                imageVector        = Icons.Default.Add,
                contentDescription = null,
                tint               = TextMuted,
                modifier           = Modifier.size(18.dp),
            )
            Spacer(Modifier.width(8.dp))
            Text(
                text  = fileName ?: "File attached",
                color = TextPri,
                fontSize = 13.sp,
            )
        }
        Spacer(Modifier.weight(1f))
        Box(
            contentAlignment = Alignment.Center,
            modifier = Modifier
                .size(28.dp)
                .background(Color(0x22FFFFFF), CircleShape)
                .clickable(role = Role.Button, onClick = onRemove),
        ) {
            Icon(
                imageVector        = Icons.Default.Close,
                contentDescription = "Remove attachment",
                tint               = TextPri,
                modifier           = Modifier.size(16.dp),
            )
        }
    }
}

/**
 * 36dp circular icon button used for attach / mic. Subtle ghost style so the
 * primary CTA (send / voice-mode) has visual hierarchy.
 */
@Composable
private fun IconPillButton(
    icon:        androidx.compose.ui.graphics.vector.ImageVector,
    contentDesc: String,
    tint:        Color,
    enabled:     Boolean,
    onClick:     () -> Unit,
) {
    Box(
        contentAlignment = Alignment.Center,
        modifier = Modifier
            .size(36.dp)
            .background(Color(0x14FFFFFF), CircleShape)
            .clickable(enabled = enabled, role = Role.Button, onClick = onClick),
    ) {
        Icon(
            imageVector        = icon,
            contentDescription = contentDesc,
            tint               = tint.copy(alpha = if (enabled) 1f else 0.4f),
            modifier           = Modifier.size(18.dp),
        )
    }
}
