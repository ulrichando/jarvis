package com.jarvis.android.presentation.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.PushPin
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import com.jarvis.android.domain.model.Conversation
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

private val ScreenBg     = Color(0xFF0D0D0D)
private val SurfaceCard  = Color(0xFF1A1A1A)
private val TextPrimary  = Color(0xFFECECEC)
private val TextMuted    = Color(0xFF8A8A8A)
private val MutedBorder  = Color(0xFF222222)
private val NewChatColor = Color(0xFFE17055)
private val DangerRed    = Color(0xFFEF4444)

/**
 * Dedicated Chats list — Claude-style. Top: hamburger + "Chats" serif title.
 * Below: search field, then a flat list of every conversation sorted
 * most-recent-first, each row = title + relative time. Tapping a row navigates
 * to the chat with that conversation selected. A coral "+ New chat" FAB sits
 * bottom-right.
 *
 * Long-pressing a row opens a context menu (Rename / Star / Delete) so the
 * full set of conversation actions stays one gesture away even when the chat
 * top-bar overflow isn't reachable from this screen.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun ChatsScreen(
    onBack:          () -> Unit,
    onOpenDrawer:    () -> Unit = onBack,
    onSelectChat:    (String) -> Unit,
    viewModel:       ChatsViewModel = hiltViewModel(),
) {
    val state by viewModel.uiState.collectAsState()

    var renameId    by remember { mutableStateOf<String?>(null) }
    var renameDraft by remember { mutableStateOf("") }
    var deleteId    by remember { mutableStateOf<String?>(null) }
    var deleteTitle by remember { mutableStateOf("") }
    var menuForId   by remember { mutableStateOf<String?>(null) }

    val filtered = remember(state.conversations, state.query) {
        val q = state.query.trim()
        val sorted = state.conversations.sortedByDescending { it.updatedAt }
        if (q.isEmpty()) sorted else sorted.filter { it.title.contains(q, ignoreCase = true) }
    }

    Scaffold(
        containerColor = ScreenBg,
        floatingActionButton = {
            ExtendedFloatingActionButton(
                onClick       = { viewModel.newConversation { c -> onSelectChat(c.id) } },
                containerColor = NewChatColor,
                contentColor   = Color.White,
                icon          = { Icon(Icons.Default.Add, contentDescription = null) },
                text          = { Text("New chat", fontWeight = FontWeight.Medium) },
            )
        },
    ) { padding ->
        Column(modifier = Modifier.padding(padding).fillMaxSize().background(ScreenBg)) {

            // ── Top bar: hamburger + title ────────────────────────────────
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(start = 4.dp, end = 8.dp, top = 8.dp, bottom = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                IconButton(onClick = onOpenDrawer) {
                    Icon(Icons.Default.Menu, contentDescription = "Open drawer", tint = TextPrimary)
                }
                Spacer(Modifier.weight(1f))
            }

            Text(
                text  = "Chats",
                style = MaterialTheme.typography.headlineMedium.copy(
                    fontFamily = FontFamily.Serif,
                    fontWeight = FontWeight.Bold,
                    fontSize   = 32.sp,
                ),
                color    = TextPrimary,
                modifier = Modifier.padding(start = 20.dp, end = 16.dp, top = 4.dp, bottom = 14.dp),
            )

            // ── Search field ──────────────────────────────────────────────
            OutlinedTextField(
                value         = state.query,
                onValueChange = viewModel::onQueryChange,
                placeholder   = { Text("Search Chats", color = TextMuted) },
                singleLine    = true,
                leadingIcon   = { Icon(Icons.Default.Search, contentDescription = null, tint = TextMuted) },
                shape         = RoundedCornerShape(12.dp),
                colors        = TextFieldDefaults.colors(
                    focusedContainerColor   = SurfaceCard,
                    unfocusedContainerColor = SurfaceCard,
                    focusedIndicatorColor   = Color.Transparent,
                    unfocusedIndicatorColor = Color.Transparent,
                    cursorColor             = TextPrimary,
                    focusedTextColor        = TextPrimary,
                    unfocusedTextColor      = TextPrimary,
                ),
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 4.dp),
            )

            Spacer(Modifier.height(6.dp))

            // ── Conversation list ─────────────────────────────────────────
            if (filtered.isEmpty()) {
                Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text(
                        if (state.conversations.isEmpty()) "No conversations yet"
                        else "No matches for \"${state.query}\"",
                        color = TextMuted,
                    )
                }
            } else {
                LazyColumn(
                    modifier            = Modifier.fillMaxSize(),
                    contentPadding      = androidx.compose.foundation.layout.PaddingValues(
                        start = 0.dp, end = 0.dp, top = 0.dp, bottom = 96.dp,
                    ),
                    verticalArrangement = Arrangement.spacedBy(0.dp),
                ) {
                    items(items = filtered, key = { it.id }) { conv ->
                        Box {
                            ConversationRow(
                                conversation = conv,
                                onClick      = { onSelectChat(conv.id) },
                                onLongClick  = { menuForId = conv.id },
                            )
                            DropdownMenu(
                                expanded         = menuForId == conv.id,
                                onDismissRequest = { menuForId = null },
                                modifier         = Modifier.background(Color(0xFF1C1C1E)),
                            ) {
                                DropdownMenuItem(
                                    text    = { Text("Rename", color = TextPrimary) },
                                    onClick = {
                                        menuForId   = null
                                        renameId    = conv.id
                                        renameDraft = conv.title
                                    },
                                )
                                DropdownMenuItem(
                                    text    = {
                                        Text(if (conv.isPinned) "Unstar" else "Star", color = TextPrimary)
                                    },
                                    onClick = {
                                        menuForId = null
                                        viewModel.togglePin(conv.id, !conv.isPinned)
                                    },
                                )
                                DropdownMenuItem(
                                    text    = { Text("Delete", color = DangerRed) },
                                    onClick = {
                                        menuForId   = null
                                        deleteId    = conv.id
                                        deleteTitle = conv.title
                                    },
                                )
                            }
                        }
                    }
                }
            }
        }

        // ── Rename dialog ──────────────────────────────────────────────────
        renameId?.let { id ->
            AlertDialog(
                onDismissRequest = { renameId = null },
                containerColor   = Color(0xFF141414),
                title = { Text("Rename conversation", color = TextPrimary) },
                text = {
                    OutlinedTextField(
                        value         = renameDraft,
                        onValueChange = { renameDraft = it },
                        singleLine    = true,
                        label         = { Text("Title") },
                    )
                },
                confirmButton = {
                    TextButton(onClick = {
                        val t = renameDraft.trim()
                        if (t.isNotBlank()) viewModel.rename(id, t)
                        renameId = null
                    }) { Text("Save", color = NewChatColor) }
                },
                dismissButton = {
                    TextButton(onClick = { renameId = null }) {
                        Text("Cancel", color = TextMuted)
                    }
                },
            )
        }

        // ── Delete confirmation ───────────────────────────────────────────
        deleteId?.let { id ->
            AlertDialog(
                onDismissRequest = { deleteId = null },
                containerColor   = Color(0xFF141414),
                title = { Text("Delete conversation?", color = TextPrimary) },
                text  = {
                    Text(
                        "\"$deleteTitle\" will be permanently deleted.",
                        color = TextMuted,
                    )
                },
                confirmButton = {
                    TextButton(onClick = {
                        viewModel.delete(id)
                        deleteId = null
                    }) { Text("Delete", color = DangerRed) }
                },
                dismissButton = {
                    TextButton(onClick = { deleteId = null }) {
                        Text("Cancel", color = TextMuted)
                    }
                },
            )
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun ConversationRow(
    conversation: Conversation,
    onClick:      () -> Unit,
    onLongClick:  () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .combinedClickable(onClick = onClick, onLongClick = onLongClick)
            .padding(horizontal = 20.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text     = conversation.title.ifBlank { "Untitled" },
                style    = MaterialTheme.typography.bodyLarge.copy(fontSize = 15.sp),
                color    = TextPrimary,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Spacer(Modifier.height(2.dp))
            Text(
                text  = relativeTime(conversation.updatedAt),
                style = MaterialTheme.typography.bodySmall.copy(fontSize = 12.sp),
                color = TextMuted,
            )
        }
        if (conversation.isPinned) {
            Icon(
                imageVector        = Icons.Default.PushPin,
                contentDescription = "Pinned",
                tint               = NewChatColor,
                modifier           = Modifier.size(14.dp),
            )
        }
    }
}

private fun relativeTime(ms: Long): String {
    val now  = System.currentTimeMillis()
    val diff = now - ms
    return when {
        diff < 60_000L                -> "Just now"
        diff < 3_600_000L             -> "${diff / 60_000} minute${pl(diff / 60_000)} ago"
        diff < 86_400_000L            -> "${diff / 3_600_000} hour${pl(diff / 3_600_000)} ago"
        diff < 7L * 86_400_000L       -> "${diff / 86_400_000} day${pl(diff / 86_400_000)} ago"
        else                          -> SimpleDateFormat("MMM d", Locale.getDefault()).format(Date(ms))
    }
}

private fun pl(n: Long): String = if (n == 1L) "" else "s"
