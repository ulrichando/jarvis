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

// Figma-matched bubble colors
private val UserBubbleBg   = Color(0xFF212121)
private val AssistBubbleBg = Color(0xFF171717)
private val BubbleBorder   = Color(0xFF2E2E2E)
private val UserBubbleShape   = RoundedCornerShape(12.dp)
private val AssistBubbleShape = RoundedCornerShape(16.dp)

/**
 * Renders a single conversation turn as a Material3 message bubble.
 *
 * User messages:  right-aligned, primary container colour, flat top-right corner.
 * Assistant messages: left-aligned, surface container colour, flat top-left corner.
 *
 * For assistant turns that include `tool_use` or `tool_result` content, a
 * collapsible "Tool calls" section is shown below the text body.
 *
 * @param isStreaming   When true, appends a [StreamingCursor] at the text tail.
 * @param streamingText If non-null, overrides [message.content] for in-progress turns.
 */
@Composable
fun MessageBubble(
    message:       Message,
    modifier:      Modifier = Modifier,
    isStreaming:   Boolean = false,
    streamingText: String? = null,
) {
    val isUser = message.role == MessageRole.USER
    val maxWidth = (LocalConfiguration.current.screenWidthDp * 0.82).dp

    Row(
        modifier = modifier.fillMaxWidth(),
        horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start,
    ) {
        Column(
            modifier = Modifier
                .widthIn(max = maxWidth)
                .background(
                    color = if (isUser) UserBubbleBg else AssistBubbleBg,
                    shape = if (isUser) UserBubbleShape else AssistBubbleShape,
                )
                .border(
                    width = 1.dp,
                    color = BubbleBorder,
                    shape = if (isUser) UserBubbleShape else AssistBubbleShape,
                )
                .padding(horizontal = 12.dp, vertical = 8.dp),
        ) {
            val bodyText = streamingText ?: message.content

            // Main content
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
                            if (bodyText.isNotBlank()) {
                                Spacer(Modifier.size(4.dp, 16.dp))
                            }
                            StreamingCursor(visible = isStreaming)
                        }
                    }
                }
                else -> {
                    Text(
                        text  = bodyText,
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
            }

            // Tool calls — collapsible section
            if (message.contentType == MessageContentType.TOOL_USE ||
                message.contentType == MessageContentType.MIXED ||
                message.contentType == MessageContentType.TOOL_RESULT) {
                ToolCallsSection(message = message)
            }
        }
    }
}

// ── Tool calls collapsible ────────────────────────────────────────────────────

@Composable
private fun ToolCallsSection(message: Message) {
    var expanded by remember { mutableStateOf(false) }
    val json = message.toolCallsJson ?: return

    Spacer(Modifier.height(4.dp))

    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            imageVector = Icons.Default.Build,
            contentDescription = null,
            tint     = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.size(14.dp),
        )
        Text(
            text  = if (message.contentType == MessageContentType.TOOL_RESULT)
                        "Tool results" else "Tool calls",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.weight(1f).padding(start = 4.dp),
        )
        IconButton(onClick = { expanded = !expanded }, modifier = Modifier.size(24.dp)) {
            Icon(
                imageVector = if (expanded) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                contentDescription = if (expanded) "Collapse" else "Expand",
                tint     = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(16.dp),
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
                    color = MaterialTheme.colorScheme.surfaceContainerHighest,
                    shape = MaterialTheme.shapes.small,
                )
                .padding(8.dp),
        ) {
            Text(
                text  = json,
                style = JarvisTheme.typography.codeInline,
                color = MaterialTheme.colorScheme.onSurface,
            )
        }
    }
}
