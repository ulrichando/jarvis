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
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AddCircleOutline
import androidx.compose.material.icons.filled.ChatBubbleOutline
import androidx.compose.material.icons.filled.Code
import androidx.compose.material.icons.filled.FolderOpen
import androidx.compose.material.icons.filled.Memory
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Terminal
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.jarvis.android.domain.model.Conversation

// ── Drawer tokens — always dark, independent of the Material scheme ──────────
// Colours align with the rest of the Jarvis UI (Accent = the blue used in
// chips, send buttons, the amplitude glow). No coral / orange any more —
// that was borrowed from Claude's brand and clashed with Jarvis's palette.
private val DrawerBg          = Color(0xFF0D0D0D)
private val DrawerBorder      = Color(0xFF222222)
private val DrawerTextPri     = Color(0xFFECECEC)
private val DrawerTextSec     = Color(0xFF8A8A8A)
private val DrawerMuted       = Color(0xFF6E6E6E)
private val Accent            = Color(0xFF1E7FFF)   // same as chat Accent
private val SelectedRowBg     = Color(0x141E7FFF)   // subtle 8% blue — not the loud dark pill
private val UserAvatarBg      = Accent

/**
 * Navigation drawer modelled on the Claude mobile drawer:
 *
 * ```
 *   Jarvis                       ← large serif title
 *
 *   ⊕  New chat                  ← highlighted CTA
 *   💬  Chats
 *   🧠  Models
 *   📁  Files
 *   ⌨️  Terminal
 *   </>  Code
 *   ────────────────────
 *   RECENTS
 *     How many S&P 500 ETFs to own
 *     Greeting exchange
 *     Untitled
 *     …
 *
 *   (UA)  Ulrich                          ⚙
 * ```
 *
 * The bottom row is the only entry point to Settings — no overflow on the chat
 * top bar is needed any more.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun ConversationDrawer(
    conversations:        List<Conversation>,
    activeId:             String?,
    onSelect:             (Conversation) -> Unit,
    onNewConversation:    () -> Unit,
    onLongClick:          (Conversation) -> Unit = {},
    onOpenSettings:       (() -> Unit)? = null,
    onOpenChats:          (() -> Unit)? = null,
    onOpenLocalAi:        (() -> Unit)? = null,
    onOpenFiles:          (() -> Unit)? = null,
    onOpenTerminal:       (() -> Unit)? = null,
    onOpenAppBuilder:     (() -> Unit)? = null,
    userName:             String? = null,
    // null by default so NOTHING is highlighted on open. Callers that
    // actually know the current route can pass e.g. DrawerRoute.Models to
    // light up that row. The previous default (Chats) misled users into
    // thinking "Chats" was a separate destination they'd navigated to.
    activeRoute:          DrawerRoute? = null,
    modifier:             Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .width(304.dp)
            .fillMaxHeight()
            .background(DrawerBg)
            .statusBarsPadding(),
    ) {
        // ── Brand header ──────────────────────────────────────────────────
        Text(
            text  = "Jarvis",
            style = MaterialTheme.typography.headlineMedium.copy(
                fontFamily = FontFamily.Serif,
                fontWeight = FontWeight.Bold,
                fontSize   = 30.sp,
            ),
            color    = DrawerTextPri,
            modifier = Modifier.padding(start = 20.dp, end = 16.dp, top = 14.dp, bottom = 12.dp),
        )

        // ── Primary nav items ─────────────────────────────────────────────
        NavItem(
            icon       = Icons.Default.AddCircleOutline,
            label      = "New chat",
            tint       = Accent,
            labelColor = Accent,
            onClick    = onNewConversation,
        )
        NavItem(
            icon     = Icons.Default.ChatBubbleOutline,
            label    = "Chats",
            selected = activeRoute == DrawerRoute.Chats,
            onClick  = { onOpenChats?.invoke() },
        )
        if (onOpenLocalAi != null) NavItem(
            icon     = Icons.Default.Memory,
            label    = "Models",
            selected = activeRoute == DrawerRoute.Models,
            onClick  = onOpenLocalAi,
        )
        if (onOpenFiles != null) NavItem(
            icon     = Icons.Default.FolderOpen,
            label    = "Files",
            selected = activeRoute == DrawerRoute.Files,
            onClick  = onOpenFiles,
        )
        if (onOpenTerminal != null) NavItem(
            icon     = Icons.Default.Terminal,
            label    = "Terminal",
            selected = activeRoute == DrawerRoute.Terminal,
            onClick  = onOpenTerminal,
        )
        if (onOpenAppBuilder != null) NavItem(
            icon     = Icons.Default.Code,
            label    = "Code",
            selected = activeRoute == DrawerRoute.Code,
            onClick  = onOpenAppBuilder,
        )

        Spacer(Modifier.height(10.dp))
        HorizontalDivider(
            color     = DrawerBorder,
            thickness = 1.dp,
            modifier  = Modifier.padding(horizontal = 16.dp),
        )

        // ── Recents — flat, untimebucketed list (Claude style) ────────────
        Text(
            text  = "Recents",
            style = MaterialTheme.typography.labelMedium.copy(
                fontWeight    = FontWeight.SemiBold,
                fontSize      = 12.sp,
                letterSpacing = 0.4.sp,
            ),
            color    = DrawerMuted,
            modifier = Modifier.padding(start = 20.dp, end = 16.dp, top = 14.dp, bottom = 6.dp),
        )

        if (conversations.isEmpty()) {
            EmptyDrawer(modifier = Modifier.weight(1f))
        } else {
            // Sort by most-recent-first; flat list, no per-row icons.
            val ordered = remember(conversations) {
                conversations.sortedByDescending { it.updatedAt }
            }
            LazyColumn(
                modifier            = Modifier.weight(1f),
                verticalArrangement = Arrangement.spacedBy(0.dp),
            ) {
                items(
                    count = ordered.size,
                    key   = { idx -> ordered[idx].id },
                ) { idx ->
                    val conv = ordered[idx]
                    RecentRow(
                        conversation = conv,
                        isActive     = conv.id == activeId,
                        onClick      = { onSelect(conv) },
                        onLongClick  = { onLongClick(conv) },
                    )
                }
            }
        }

        // ── Bottom row: avatar + name + settings gear ─────────────────────
        if (onOpenSettings != null) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(start = 16.dp, end = 8.dp, top = 8.dp, bottom = 8.dp)
                    .navigationBarsPadding(),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Box(
                    modifier = Modifier
                        .size(34.dp)
                        .background(UserAvatarBg, CircleShape),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        text  = (userName?.take(2)?.uppercase() ?: "UA"),
                        style = MaterialTheme.typography.labelMedium.copy(
                            fontWeight = FontWeight.SemiBold,
                            fontSize   = 12.sp,
                        ),
                        color = Color.White,
                    )
                }
                Spacer(Modifier.width(12.dp))
                Text(
                    text     = userName ?: "Ulrich",
                    style    = MaterialTheme.typography.bodyLarge.copy(fontSize = 15.sp),
                    color    = DrawerTextPri,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                IconButton(onClick = onOpenSettings) {
                    Icon(
                        imageVector        = Icons.Default.Settings,
                        contentDescription = "Open settings",
                        tint               = DrawerTextSec,
                        modifier           = Modifier.size(22.dp),
                    )
                }
            }
        }
    }
}

/** Top-level destination the drawer can be sitting on; drives the selected pill. */
enum class DrawerRoute { Chats, Models, Files, Terminal, Code }

@Composable
private fun NavItem(
    icon:       ImageVector,
    label:      String,
    onClick:    () -> Unit,
    selected:   Boolean = false,
    tint:       Color   = DrawerTextPri,
    labelColor: Color   = DrawerTextPri,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 2.dp)
            .background(
                color = if (selected) SelectedRowBg else Color.Transparent,
                shape = RoundedCornerShape(28.dp),
            )
            .clickable(onClick = onClick)
            .padding(horizontal = 14.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            imageVector        = icon,
            contentDescription = null,
            tint               = tint,
            modifier           = Modifier.size(22.dp),
        )
        Spacer(Modifier.width(16.dp))
        Text(
            text  = label,
            style = MaterialTheme.typography.bodyLarge.copy(fontSize = 15.sp),
            color = labelColor,
        )
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun RecentRow(
    conversation: Conversation,
    isActive:     Boolean,
    onClick:      () -> Unit,
    onLongClick:  () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .combinedClickable(onClick = onClick, onLongClick = onLongClick)
            .padding(horizontal = 20.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text     = conversation.title.ifBlank { "Untitled" },
            style    = MaterialTheme.typography.bodyMedium.copy(
                fontSize = 14.sp,
                fontWeight = if (isActive) FontWeight.SemiBold else FontWeight.Normal,
            ),
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            color    = if (isActive) DrawerTextPri else DrawerTextPri.copy(alpha = 0.92f),
        )
    }
}

@Composable
private fun EmptyDrawer(modifier: Modifier = Modifier) {
    Column(
        modifier              = modifier.fillMaxWidth().padding(24.dp),
        horizontalAlignment   = Alignment.CenterHorizontally,
        verticalArrangement   = Arrangement.Center,
    ) {
        Text(
            text  = "No conversations yet",
            style = MaterialTheme.typography.bodyMedium,
            color = DrawerTextSec,
        )
        Spacer(Modifier.height(4.dp))
        Text(
            text  = "Start one above to see it here.",
            style = MaterialTheme.typography.bodySmall,
            color = DrawerMuted,
        )
    }
}
