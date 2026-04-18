package com.jarvis.android.presentation.components

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PushPin
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.jarvis.android.domain.model.Conversation
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

// ── Drawer design tokens (always dark — independent of system theme) ───────────
private val DrawerBg       = Color(0xFF0D0D0D)
private val DrawerItemBg   = Color(0xFF171717)
private val DrawerActiveBg = Color(0x1A1E7FFF)   // 10 % blue
private val DrawerDivider  = Color(0xFF2E2E2E)
private val DrawerTextPri  = Color(0xFFD9D9D9)
private val DrawerTextSec  = Color(0xFF7E7E7E)
private val DrawerBlue     = Color(0xFF1E7FFF)
private val DrawerGold     = Color(0xFFC8CDD6)   // ghost silver (matches icon)

/**
 * Navigation drawer content showing the list of [conversations].
 *
 * Used inside a `ModalNavigationDrawer`. Each item shows the conversation
 * title, model, last-updated timestamp, and a pin indicator.
 *
 * Long-pressing an item reveals rename/delete/pin actions (handled externally
 * via [onLongClick] → bottom sheet or dialog in the parent screen).
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun ConversationDrawer(
    conversations:     List<Conversation>,
    activeId:          String?,
    onSelect:          (Conversation) -> Unit,
    onNewConversation: () -> Unit,
    onLongClick:       (Conversation) -> Unit = {},
    modifier:          Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .width(300.dp)
            .fillMaxHeight()
            .background(DrawerBg)
            .statusBarsPadding(),
    ) {
        // Header
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text  = "Jarvis",
                style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold),
                color = DrawerGold,
            )
        }

        HorizontalDivider(color = DrawerDivider)

        // Conversation list
        LazyColumn(modifier = Modifier.weight(1f)) {
            items(
                items = conversations,
                key   = { it.id },
            ) { conv ->
                val isActive = conv.id == activeId
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(if (isActive) DrawerActiveBg else DrawerItemBg)
                        .combinedClickable(
                            onClick     = { onSelect(conv) },
                            onLongClick = { onLongClick(conv) },
                        )
                        .padding(horizontal = 16.dp, vertical = 10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(
                            text     = conv.title,
                            style    = MaterialTheme.typography.bodyMedium,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                            color    = if (isActive) DrawerBlue else DrawerTextPri,
                        )
                        Text(
                            text  = formatTimestamp(conv.updatedAt),
                            style = MaterialTheme.typography.labelSmall,
                            color = DrawerTextSec,
                        )
                    }
                    if (conv.isPinned) {
                        Icon(
                            imageVector        = Icons.Default.PushPin,
                            contentDescription = "Pinned",
                            tint               = DrawerBlue,
                            modifier           = Modifier.size(14.dp),
                        )
                    }
                }
            }
        }

        Spacer(Modifier.height(8.dp))
    }
}

private fun formatTimestamp(ms: Long): String {
    val now  = System.currentTimeMillis()
    val diff = now - ms
    return when {
        diff < 60_000L          -> "Just now"
        diff < 3_600_000L       -> "${diff / 60_000}m ago"
        diff < 86_400_000L      -> "${diff / 3_600_000}h ago"
        diff < 604_800_000L     -> "${diff / 86_400_000}d ago"
        else -> SimpleDateFormat("MMM d", Locale.getDefault()).format(Date(ms))
    }
}
