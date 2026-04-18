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
import androidx.compose.material.icons.filled.AttachFile
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
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

private val InputBg           = Color(0xFF212121)
private val InputBorder       = Color(0x1FFFFFFF)   // white 12 %
private val BubbleBorder      = Color(0xFF2E2E2E)
private val TextPrimary       = Color(0xFFD9D9D9)
private val TextHint          = Color(0x7F7E7E7E)   // #7e7e7e at 50 %
private val TextMuted         = Color(0xFF7E7E7E)
private val SendActiveBg      = Color(0xFFD9D9D9)   // white-ish fill when text present
private val SendInactiveBg    = Color(0x14FFFFFF)   // 8 % white
private val DeepThinkBlue     = Color(0xFF1E7FFF)
private val DeepThinkBorder   = Color(0xFF1E7FFF)
private val DeepThinkBg       = Color(0x0A1E7FFF)   // 4 % blue tint
private val ChipInactiveBorder = Color(0x0DFFFFFF)  // 5 % white
private val ChipInactiveBg    = Color(0x08FFFFFF)   // 3 % white

/**
 * JARVIS chat input bar — Figma "Nirmala - AI Assistant" design.
 *
 * Layout:
 * ```
 * ┌─────────────────────────────────────────────────────┐
 * │  [text field…]             [📎 attach]  [⬆ send]   │
 * │  [🔵 Deep Think] [🔍 Search]      [Claude 3.5  ▾]  │
 * └─────────────────────────────────────────────────────┘
 * ```
 *
 * - Send button is white-filled when [text] is non-blank; dim otherwise.
 * - While [isStreaming], the send button becomes a Stop button (red).
 * - "Deep Think" and "Search" are local toggle chips (no ViewModel needed yet).
 *
 * @param text          Current input field content.
 * @param onTextChange  Called on every keystroke.
 * @param onSend        Called when the user taps the Send button.
 * @param onStop        Called when the user taps Stop during streaming.
 * @param onAttach      Optional attachment callback; hides attach icon when null.
 * @param isStreaming   True while a response turn is in flight.
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
    placeholder:    String  = "Let's talk",
    onVoice:        (() -> Unit)? = null,
    isRecording:    Boolean = false,
    ttsEnabled:     Boolean = true,
    onToggleTts:    (() -> Unit)? = null,
    routingLabel:   String  = "Auto",
    onCycleRouting: (() -> Unit)? = null,
) {
    var isDeepThink by remember { mutableStateOf(false) }
    var isSearch    by remember { mutableStateOf(false) }

    val hasText = text.isNotBlank()

    Box(
        modifier = modifier
            .fillMaxWidth()
            .background(Color(0xFF0D0D0D)), // match screen background below card
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .padding(start = 16.dp, end = 16.dp, top = 4.dp, bottom = 12.dp),
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(InputBg, RoundedCornerShape(20.dp))
                    .border(1.dp, InputBorder, RoundedCornerShape(20.dp))
                    .padding(horizontal = 16.dp, vertical = 12.dp),
                verticalArrangement = Arrangement.spacedBy(16.dp),
            ) {
                // ── Row 1: text field + action buttons ────────────────────────
                Row(
                    modifier          = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    BasicTextField(
                        value         = text,
                        onValueChange = onTextChange,
                        enabled       = enabled && !isStreaming,
                        textStyle     = MaterialTheme.typography.bodyMedium.copy(
                            color    = TextPrimary,
                            fontSize = 14.sp,
                        ),
                        cursorBrush = SolidColor(DeepThinkBlue),
                        keyboardOptions = KeyboardOptions(
                            capitalization = KeyboardCapitalization.Sentences,
                            imeAction      = ImeAction.Default,
                        ),
                        maxLines = 6,
                        modifier = Modifier
                            .weight(1f)
                            .heightIn(min = 20.dp, max = 120.dp),
                        decorationBox = { inner ->
                            Box(contentAlignment = Alignment.CenterStart) {
                                if (text.isEmpty()) {
                                    Text(
                                        text  = placeholder,
                                        style = MaterialTheme.typography.bodyMedium.copy(
                                            fontSize = 14.sp,
                                        ),
                                        color = TextHint,
                                    )
                                }
                                inner()
                            }
                        },
                    )

                    // Attach icon
                    if (onAttach != null) {
                        Spacer(Modifier.width(12.dp))
                        Box(
                            contentAlignment = Alignment.Center,
                            modifier         = Modifier
                                .size(28.dp)
                                .clickable(
                                    enabled = enabled && !isStreaming,
                                    role    = Role.Button,
                                    onClick = onAttach,
                                ),
                        ) {
                            Icon(
                                imageVector        = Icons.Default.AttachFile,
                                contentDescription = "Attach file",
                                tint               = TextMuted,
                                modifier           = Modifier.size(20.dp),
                            )
                        }
                    }

                    // Mic button — visible when not streaming (hidden while AI is responding)
                    if (onVoice != null && !isStreaming) {
                        Spacer(Modifier.width(8.dp))
                        Box(
                            contentAlignment = Alignment.Center,
                            modifier = Modifier
                                .size(34.dp)
                                .background(
                                    color = if (isRecording) Color(0xFF1E7FFF).copy(alpha = 0.18f) else SendInactiveBg,
                                    shape = CircleShape,
                                )
                                .border(
                                    width = 1.dp,
                                    color = if (isRecording) Color(0xFF1E7FFF).copy(alpha = 0.6f) else Color(0x0AFFFFFF),
                                    shape = CircleShape,
                                )
                                .clickable(enabled = enabled, role = Role.Button, onClick = onVoice),
                        ) {
                            Icon(
                                imageVector        = if (isRecording) Icons.Default.Stop else Icons.Default.Mic,
                                contentDescription = if (isRecording) "Stop recording" else "Voice input",
                                tint               = if (isRecording) Color(0xFF1E7FFF) else TextMuted,
                                modifier           = Modifier.size(16.dp),
                            )
                        }
                    }

                    Spacer(Modifier.width(8.dp))

                    // Send / Stop button — filled circle
                    Box(
                        contentAlignment = Alignment.Center,
                        modifier = Modifier
                            .size(34.dp)
                            .background(
                                color = when {
                                    isStreaming -> MaterialTheme.colorScheme.error.copy(alpha = 0.15f)
                                    hasText     -> SendActiveBg
                                    else        -> SendInactiveBg
                                },
                                shape = CircleShape,
                            )
                            .border(
                                width = 1.dp,
                                color = Color(0x0AFFFFFF),
                                shape = CircleShape,
                            )
                            .clickable(
                                enabled = enabled && (isStreaming || hasText),
                                role    = Role.Button,
                                onClick = if (isStreaming) onStop else onSend,
                            ),
                    ) {
                        Icon(
                            imageVector        = if (isStreaming) Icons.Default.Stop
                                                 else Icons.AutoMirrored.Filled.Send,
                            contentDescription = if (isStreaming) "Stop" else "Send",
                            tint               = when {
                                isStreaming -> MaterialTheme.colorScheme.error
                                hasText     -> Color(0xFF0D0D0D)
                                else        -> Color.White.copy(alpha = 0.35f)
                            },
                            modifier           = Modifier.size(16.dp),
                        )
                    }
                }

                // ── Row 2: mode chips + model selector ────────────────────────
                Row(
                    modifier              = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment     = Alignment.CenterVertically,
                ) {
                    // Left: Deep Think + Search chips
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        ModeChip(
                            label   = "Deep Think",
                            active  = isDeepThink,
                            onClick = { isDeepThink = !isDeepThink },
                        )
                        ModeChip(
                            label   = "Search",
                            active  = isSearch,
                            onClick = { isSearch = !isSearch },
                        )
                    }

                    // Right: Model / routing selector
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        modifier          = Modifier.clickable(role = Role.Button) { onCycleRouting?.invoke() },
                    ) {
                        // TTS indicator dot
                        if (onToggleTts != null) {
                            Box(
                                modifier = Modifier
                                    .size(7.dp)
                                    .background(
                                        color = if (ttsEnabled) Color(0xFF1E7FFF) else TextMuted.copy(alpha = 0.4f),
                                        shape = CircleShape,
                                    )
                                    .clickable { onToggleTts() }
                            )
                            Spacer(Modifier.width(5.dp))
                        }
                        Text(
                            text  = routingLabel,
                            style = MaterialTheme.typography.labelSmall.copy(fontSize = 13.sp),
                            color = TextMuted,
                        )
                        Spacer(Modifier.width(2.dp))
                        Icon(
                            imageVector        = Icons.Default.KeyboardArrowDown,
                            contentDescription = "Cycle routing mode",
                            tint               = TextMuted,
                            modifier           = Modifier.size(16.dp),
                        )
                    }
                }
            }
        }
    }
}

// ── Mode toggle chip ──────────────────────────────────────────────────────────

@Composable
private fun ModeChip(
    label:   String,
    active:  Boolean,
    onClick: () -> Unit,
) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier          = Modifier
            .background(
                color = if (active) DeepThinkBg else ChipInactiveBg,
                shape = RoundedCornerShape(999.dp),
            )
            .border(
                width = 1.dp,
                color = if (active) DeepThinkBorder else ChipInactiveBorder,
                shape = RoundedCornerShape(999.dp),
            )
            .clickable(role = Role.Switch, onClick = onClick)
            .padding(horizontal = 6.dp, vertical = 4.dp),
    ) {
        Text(
            text  = label,
            style = MaterialTheme.typography.labelSmall.copy(fontSize = 13.sp),
            color = if (active) DeepThinkBlue else Color(0xFFAEAEAE),
        )
    }
}
