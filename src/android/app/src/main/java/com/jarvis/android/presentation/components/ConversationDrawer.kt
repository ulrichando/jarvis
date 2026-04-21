package com.jarvis.android.presentation.components

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
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
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ChatBubbleOutline
import androidx.compose.material.icons.filled.PushPin
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.jarvis.android.domain.model.Conversation
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale

// ── Drawer tokens — always dark, independent of the Material scheme ──────────
private val DrawerBg       = Color(0xFF0D0D0D)
private val DrawerHeader   = Color(0xFF141414)
private val DrawerActiveBg = Color(0x1A1E7FFF)   // 10 % blue
private val DrawerBorder   = Color(0xFF222222)
private val DrawerTextPri  = Color(0xFFECECEC)
private val DrawerTextSec  = Color(0xFF8A8A8A)
private val DrawerMuted    = Color(0xFF5F5F5F)
private val Accent         = Color(0xFF1E7FFF)

/**
 * Navigation drawer — conversation history, grouped by time bucket.
 *
 * ```
 *   ● JARVIS
 *   ┌───────────────────────┐
 *   │ +  New conversation    │   ← always-visible CTA at top
 *   └───────────────────────┘
 *
 *   TODAY
 *     ・ Network scan results
 *     ・ Log triage
 *
 *   YESTERDAY
 *     ・ Shell script refactor
 *
 *   EARLIER
 *     ・ Fresh install notes
 * ```
 *
 * Long-pressing a conversation raises [onLongClick] so the parent can open a
 * context menu (rename / delete / pin).
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
    // Group once per render — cheap even at a few hundred convos.
    val grouped = remember(conversations) { groupByTime(conversations) }

    Column(
        modifier = modifier
            .width(304.dp)
            .fillMaxHeight()
            .background(DrawerBg)
            .statusBarsPadding(),
    ) {
        // ── Brand header ──────────────────────────────────────────────────
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .background(DrawerHeader)
                .padding(horizontal = 16.dp, vertical = 14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                modifier = Modifier
                    .size(26.dp)
                    .background(Accent.copy(alpha = 0.14f), CircleShape)
                    .border(1.dp, Accent.copy(alpha = 0.35f), CircleShape),
                contentAlignment = Alignment.Center,
            ) {
                Box(modifier = Modifier.size(7.dp).background(Accent, CircleShape))
            }
            Spacer(Modifier.width(10.dp))
            Text(
                text  = "JARVIS",
                style = MaterialTheme.typography.titleMedium.copy(
                    fontWeight    = FontWeight.Bold,
                    fontSize      = 15.sp,
                    letterSpacing = 0.6.sp,
                ),
                color = DrawerTextPri,
            )
        }

        Spacer(Modifier.height(8.dp))

        // ── New conversation CTA ──────────────────────────────────────────
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp)
                .background(Accent.copy(alpha = 0.10f), RoundedCornerShape(12.dp))
                .border(1.dp, Accent.copy(alpha = 0.30f), RoundedCornerShape(12.dp))
                .clickable(onClick = onNewConversation)
                .padding(horizontal = 14.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(
                imageVector        = Icons.Default.Add,
                contentDescription = null,
                tint               = Accent,
                modifier           = Modifier.size(18.dp),
            )
            Spacer(Modifier.width(8.dp))
            Text(
                text  = "New conversation",
                style = MaterialTheme.typography.bodyMedium.copy(
                    fontWeight = FontWeight.Medium,
                    fontSize   = 14.sp,
                ),
                color = Accent,
            )
        }

        Spacer(Modifier.height(8.dp))

        // ── Conversation list, grouped by time bucket ─────────────────────
        if (conversations.isEmpty()) {
            EmptyDrawer(modifier = Modifier.weight(1f))
        } else {
            LazyColumn(
                modifier            = Modifier.weight(1f),
                verticalArrangement = Arrangement.spacedBy(1.dp),
            ) {
                grouped.forEach { (label, convos) ->
                    if (convos.isNotEmpty()) {
                        item(key = "header_$label") {
                            SectionHeader(label)
                        }
                        items(
                            count = convos.size,
                            key   = { idx -> convos[idx].id },
                        ) { idx ->
                            val conv = convos[idx]
                            DrawerRow(
                                conversation = conv,
                                isActive     = conv.id == activeId,
                                onClick      = { onSelect(conv) },
                                onLongClick  = { onLongClick(conv) },
                            )
                        }
                        item(key = "spacer_$label") { Spacer(Modifier.height(6.dp)) }
                    }
                }
            }
        }

        Spacer(Modifier.height(8.dp))
    }
}

@Composable
private fun SectionHeader(label: String) {
    Text(
        text  = label,
        style = MaterialTheme.typography.labelSmall.copy(
            fontWeight    = FontWeight.SemiBold,
            letterSpacing = 0.8.sp,
            fontSize      = 11.sp,
        ),
        color    = DrawerMuted,
        modifier = Modifier
            .fillMaxWidth()
            .padding(start = 20.dp, end = 16.dp, top = 10.dp, bottom = 4.dp),
    )
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun DrawerRow(
    conversation: Conversation,
    isActive:     Boolean,
    onClick:      () -> Unit,
    onLongClick:  () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 8.dp, vertical = 1.dp)
            .background(
                color = if (isActive) DrawerActiveBg else Color.Transparent,
                shape = RoundedCornerShape(10.dp),
            )
            .combinedClickable(onClick = onClick, onLongClick = onLongClick)
            .padding(horizontal = 10.dp, vertical = 9.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            imageVector        = Icons.Default.ChatBubbleOutline,
            contentDescription = null,
            tint               = if (isActive) Accent else DrawerMuted,
            modifier           = Modifier.size(14.dp),
        )
        Spacer(Modifier.width(10.dp))
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text     = conversation.title.ifBlank { "Untitled" },
                style    = MaterialTheme.typography.bodyMedium.copy(fontSize = 13.5.sp),
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                color    = if (isActive) DrawerTextPri else DrawerTextPri.copy(alpha = 0.85f),
                fontWeight = if (isActive) FontWeight.Medium else FontWeight.Normal,
            )
        }
        if (conversation.isPinned) {
            Icon(
                imageVector        = Icons.Default.PushPin,
                contentDescription = "Pinned",
                tint               = Accent,
                modifier           = Modifier.size(13.dp),
            )
        }
    }
}

@Composable
private fun EmptyDrawer(modifier: Modifier = Modifier) {
    Column(
        modifier              = modifier.fillMaxWidth().padding(24.dp),
        horizontalAlignment   = Alignment.CenterHorizontally,
        verticalArrangement   = Arrangement.Center,
    ) {
        Icon(
            imageVector        = Icons.Default.ChatBubbleOutline,
            contentDescription = null,
            tint               = DrawerMuted,
            modifier           = Modifier.size(36.dp),
        )
        Spacer(Modifier.height(10.dp))
        Text(
            text      = "No conversations yet",
            style     = MaterialTheme.typography.bodyMedium,
            color     = DrawerTextSec,
        )
        Spacer(Modifier.height(4.dp))
        Text(
            text      = "Start one above to see it here.",
            style     = MaterialTheme.typography.bodySmall,
            color     = DrawerMuted,
        )
    }
}

// ── Time bucketing ───────────────────────────────────────────────────────────

private val BucketOrder = listOf("TODAY", "YESTERDAY", "THIS WEEK", "THIS MONTH", "EARLIER")

/**
 * Groups [conversations] into stable, ordered buckets. Pinned items always
 * surface to the top regardless of timestamp.
 */
private fun groupByTime(
    conversations: List<Conversation>,
): Map<String, List<Conversation>> {
    val now  = System.currentTimeMillis()
    val cal  = Calendar.getInstance().apply { timeInMillis = now }
    val startOfToday     = cal.apply {
        set(Calendar.HOUR_OF_DAY, 0); set(Calendar.MINUTE, 0)
        set(Calendar.SECOND, 0); set(Calendar.MILLISECOND, 0)
    }.timeInMillis
    val startOfYesterday = startOfToday - 24L * 3600_000
    val startOfWeek      = startOfToday - 7L  * 24 * 3600_000
    val startOfMonth     = startOfToday - 30L * 24 * 3600_000

    val buckets = linkedMapOf<String, MutableList<Conversation>>().apply {
        BucketOrder.forEach { put(it, mutableListOf()) }
        put("PINNED", mutableListOf())
    }

    conversations.sortedByDescending { it.updatedAt }.forEach { conv ->
        val bucket = when {
            conv.isPinned                        -> "PINNED"
            conv.updatedAt >= startOfToday       -> "TODAY"
            conv.updatedAt >= startOfYesterday   -> "YESTERDAY"
            conv.updatedAt >= startOfWeek        -> "THIS WEEK"
            conv.updatedAt >= startOfMonth       -> "THIS MONTH"
            else                                 -> "EARLIER"
        }
        buckets[bucket]?.add(conv)
    }

    // Emit pinned first, then time buckets in canonical order.
    val ordered = linkedMapOf<String, List<Conversation>>()
    buckets["PINNED"]?.takeIf { it.isNotEmpty() }?.let { ordered["PINNED"] = it }
    BucketOrder.forEach { key ->
        buckets[key]?.takeIf { it.isNotEmpty() }?.let { ordered[key] = it }
    }
    return ordered
}

// Retained for future use (e.g. accessibility descriptions). Not rendered.
@Suppress("unused")
private fun formatTimestamp(ms: Long): String {
    val now  = System.currentTimeMillis()
    val diff = now - ms
    return when {
        diff < 60_000L       -> "Just now"
        diff < 3_600_000L    -> "${diff / 60_000}m ago"
        diff < 86_400_000L   -> "${diff / 3_600_000}h ago"
        diff < 604_800_000L  -> "${diff / 86_400_000}d ago"
        else                 -> SimpleDateFormat("MMM d", Locale.getDefault()).format(Date(ms))
    }
}
