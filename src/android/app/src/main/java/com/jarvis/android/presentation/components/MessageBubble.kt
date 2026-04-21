package com.jarvis.android.presentation.components

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.expandVertically
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Build
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
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
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.unit.dp
import com.jarvis.android.core.designsystem.JarvisTheme
import com.jarvis.android.domain.model.Message
import com.jarvis.android.domain.model.MessageContentType
import com.jarvis.android.domain.model.MessageRole

// ── Local tokens — keep in sync with ChatScreen/HomeHero ──────────────────────

private val Accent          = Color(0xFF1E7FFF)
private val UserBubbleBg    = Color(0xFF1E2A3D)   // subtle blue tint for user
private val UserBubbleShape = RoundedCornerShape(
    topStart = 18.dp, topEnd = 18.dp, bottomStart = 18.dp, bottomEnd = 4.dp,
)
private val TextPrimary     = Color(0xFFECECEC)
private val TextSecondary   = Color(0xFF8A8A8A)

/**
 * Renders a single conversation turn.
 *
 * **User messages** sit right-aligned in a rounded blue-tinted bubble with a
 * flattened bottom-right corner — a visual "tail" toward the sender.
 *
 * **Assistant messages** are bubble-less and left-aligned, preceded by a small
 * JARVIS avatar dot. Reading long AI responses in a bubble fights with the
 * monospace-heavy content they often contain; letting them flow full-width
 * makes code blocks and tool traces comfortable.
 *
 * For assistant turns that include `tool_use` or `tool_result` content, a
 * collapsible "Tool calls" section is shown below the text body.
 *
 * @param isStreaming   When true, appends a [StreamingCursor] at the text tail.
 * @param streamingText If non-null, overrides [message.content] for in-progress
 *                      turns — used by the streaming ghost bubble.
 */
@Composable
fun MessageBubble(
    message:       Message,
    modifier:      Modifier = Modifier,
    isStreaming:   Boolean = false,
    streamingText: String? = null,
) {
    val isUser   = message.role == MessageRole.USER
    val maxWidth = (LocalConfiguration.current.screenWidthDp * 0.86).dp
    val bodyText = streamingText ?: message.content

    Row(
        modifier              = modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 2.dp),
        horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start,
    ) {
        if (!isUser) {
            AssistantAvatar()
            Spacer(Modifier.size(8.dp))
        }

        Column(
            modifier = Modifier
                .widthIn(max = maxWidth)
                .let {
                    if (isUser) it
                        .background(UserBubbleBg, UserBubbleShape)
                        .padding(horizontal = 14.dp, vertical = 10.dp)
                    else it.padding(vertical = 2.dp)
                },
        ) {
            // ── Body ──────────────────────────────────────────────────────
            when {
                message.contentType == MessageContentType.TEXT ||
                message.contentType == MessageContentType.MIXED ||
                isStreaming -> {
                    if (bodyText.isNotBlank()) {
                        MarkdownText(
                            markdown = bodyText,
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                    if (isStreaming) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            if (bodyText.isNotBlank()) Spacer(Modifier.size(4.dp, 16.dp))
                            StreamingCursor(visible = true)
                        }
                    }
                }
                else -> {
                    Text(
                        text  = bodyText,
                        style = MaterialTheme.typography.bodyMedium,
                        color = TextPrimary,
                    )
                }
            }

            // ── Tool calls — collapsible below the message body ───────────
            if (message.contentType == MessageContentType.TOOL_USE ||
                message.contentType == MessageContentType.MIXED ||
                message.contentType == MessageContentType.TOOL_RESULT
            ) {
                ToolCallsSection(message = message)
            }
        }
    }
}

// ── Assistant avatar — tiny blue dot that reads as JARVIS ────────────────────

@Composable
private fun AssistantAvatar() {
    Box(
        modifier = Modifier
            .size(26.dp)
            .background(Accent.copy(alpha = 0.12f), CircleShape)
            .border(1.dp, Accent.copy(alpha = 0.3f), CircleShape),
        contentAlignment = Alignment.Center,
    ) {
        Box(
            modifier = Modifier
                .size(7.dp)
                .background(Accent, CircleShape),
        )
    }
}

// ── Tool calls collapsible ────────────────────────────────────────────────────

@Composable
private fun ToolCallsSection(message: Message) {
    var expanded by remember { mutableStateOf(false) }
    val json = message.toolCallsJson ?: return

    Spacer(Modifier.height(6.dp))

    Row(
        modifier          = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            imageVector        = Icons.Default.Build,
            contentDescription = null,
            tint               = TextSecondary,
            modifier           = Modifier.size(14.dp),
        )
        Text(
            text  = if (message.contentType == MessageContentType.TOOL_RESULT)
                        "Tool results" else "Tool calls",
            style    = MaterialTheme.typography.labelSmall,
            color    = TextSecondary,
            modifier = Modifier.weight(1f).padding(start = 4.dp),
        )
        IconButton(onClick = { expanded = !expanded }, modifier = Modifier.size(24.dp)) {
            Icon(
                imageVector        = if (expanded) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                contentDescription = if (expanded) "Collapse" else "Expand",
                tint               = TextSecondary,
                modifier           = Modifier.size(16.dp),
            )
        }
    }

    AnimatedVisibility(
        visible = expanded,
        enter   = expandVertically(),
        exit    = shrinkVertically(),
    ) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .padding(top = 4.dp)
                .background(
                    color = Color(0xFF121212),
                    shape = RoundedCornerShape(8.dp),
                )
                .border(1.dp, Color(0xFF262626), RoundedCornerShape(8.dp))
                .padding(10.dp),
        ) {
            Text(
                text  = json,
                style = JarvisTheme.typography.codeInline,
                color = TextPrimary,
            )
        }
    }
}
