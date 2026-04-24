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
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.PushPin
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.SelectAll
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
import androidx.activity.compose.BackHandler
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
private val NewChatColor = Color(0xFF1E7FFF)
private val DangerRed    = Color(0xFFEF4444)

/**
 * Dedicated Chats list — Claude-style. Long-press a row to enter multi-select
 * mode: the top bar flips to a contextual action bar with select-all and
 * bulk-delete. Tap rows to toggle selection; tap close / press back to exit.
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

    var renameId        by remember { mutableStateOf<String?>(null) }
    var renameDraft     by remember { mutableStateOf("") }
    var deleteId        by remember { mutableStateOf<String?>(null) }
    var deleteTitle     by remember { mutableStateOf("") }
    var menuForId       by remember { mutableStateOf<String?>(null) }
    var confirmBulkDel  by remember { mutableStateOf(false) }

    val filtered = remember(state.conversations, state.query) {
        val q = state.query.trim()
        val sorted = state.conversations.sortedByDescending { it.updatedAt }
        if (q.isEmpty()) sorted else sorted.filter { it.title.contains(q, ignoreCase = true) }
    }

    // Back button exits selection mode first, before navigating away.
    BackHandler(enabled = state.selectionMode) { viewModel.clearSelection() }

    Scaffold(
        containerColor = ScreenBg,
        floatingActionButton = {
            if (!state.selectionMode) {
                ExtendedFloatingActionButton(
                    onClick        = { viewModel.newConversation { c -> onSelectChat(c.id) } },
                    containerColor = NewChatColor,
                    contentColor   = Color.White,
                    icon           = { Icon(Icons.Default.Add, contentDescription = null) },
                    text           = { Text("New chat", fontWeight = FontWeight.Medium) },
                )
            }
        },
    ) { padding ->
        Column(modifier = Modifier.padding(padding).fillMaxSize().background(ScreenBg)) {

            // ── Top bar ───────────────────────────────────────────────────
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(start = 4.dp, end = 8.dp, top = 8.dp, bottom = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                if (state.selectionMode) {
                    // Contextual action bar — close / count / select-all / delete
                    IconButton(onClick = { viewModel.clearSelection() }) {
                        Icon(Icons.Default.Close, contentDescription = "Cancel selection", tint = TextPrimary)
                    }
                    Text(
                        text  = "${state.selectedIds.size} selected",
                        style = MaterialTheme.typography.titleMedium,
                        color = TextPrimary,
                    )
                    Spacer(Modifier.weight(1f))
                    IconButton(onClick = {
                        viewModel.selectAllVisible(filtered.map { it.id })
                    }) {
                        Icon(Icons.Default.SelectAll, contentDescription = "Select all", tint = TextPrimary)
                    }
                    IconButton(onClick = { confirmBulkDel = true }) {
                        Icon(Icons.Default.Delete, contentDescription = "Delete selected", tint = DangerRed)
                    }
                } else {
                    IconButton(onClick = onOpenDrawer) {
                        Icon(Icons.Default.Menu, contentDescription = "Open drawer", tint = TextPrimary)
                    }
                    Spacer(Modifier.weight(1f))
                }
            }

            if (!state.selectionMode) {
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
            }

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
                        val selected = state.selectedIds.contains(conv.id)
                        Box {
                            ConversationRow(
                                conversation = conv,
                                selected     = selected,
                                selectionMode = state.selectionMode,
                                onClick      = {
                                    if (state.selectionMode) viewModel.toggleSelection(conv.id)
                                    else                     onSelectChat(conv.id)
                                },
                                onLongClick  = {
                                    // Long-press always toggles selection. First long-press
                                    // enters selection mode; subsequent ones add/remove rows
                                    // (or tap does the same once mode is active).
                                    if (!state.selectionMode) viewModel.toggleSelection(conv.id)
                                    else                      menuForId = conv.id
                                },
                            )
                            // Per-row overflow menu only available when NOT in selection mode.
                            if (!state.selectionMode) {
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
                                        text    = { Text("Select", color = TextPrimary) },
                                        onClick = {
                                            menuForId = null
                                            viewModel.toggleSelection(conv.id)
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
        }

        // ── Rename dialog ─────────────────────────────────────────────────
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

        // ── Delete single ─────────────────────────────────────────────────
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

        // ── Delete bulk ───────────────────────────────────────────────────
        if (confirmBulkDel) {
            val n = state.selectedIds.size
            AlertDialog(
                onDismissRequest = { confirmBulkDel = false },
                containerColor   = Color(0xFF141414),
                title = { Text("Delete $n conversation${if (n == 1) "" else "s"}?", color = TextPrimary) },
                text  = {
                    Text(
                        "This cannot be undone.",
                        color = TextMuted,
                    )
                },
                confirmButton = {
                    TextButton(onClick = {
                        viewModel.deleteSelected()
                        confirmBulkDel = false
                    }) { Text("Delete $n", color = DangerRed) }
                },
                dismissButton = {
                    TextButton(onClick = { confirmBulkDel = false }) {
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
    conversation:  Conversation,
    selected:      Boolean,
    selectionMode: Boolean,
    onClick:       () -> Unit,
    onLongClick:   () -> Unit,
) {
    val rowBg = if (selected) NewChatColor.copy(alpha = 0.18f) else Color.Transparent
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(rowBg)
            .combinedClickable(onClick = onClick, onLongClick = onLongClick)
            .padding(horizontal = 20.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (selectionMode) {
            // Leading checkbox circle so the selection is legible at a glance.
            val tick = if (selected) NewChatColor else Color(0xFF333333)
            Box(
                modifier = Modifier
                    .size(22.dp)
                    .background(
                        color = if (selected) NewChatColor else Color.Transparent,
                        shape = RoundedCornerShape(11.dp),
                    ),
                contentAlignment = Alignment.Center,
            ) {
                if (selected) {
                    Icon(
                        imageVector        = Icons.Default.Check,
                        contentDescription = null,
                        tint               = Color.White,
                        modifier           = Modifier.size(14.dp),
                    )
                } else {
                    Box(
                        modifier = Modifier
                            .size(20.dp)
                            .background(Color.Transparent, RoundedCornerShape(10.dp))
                            .padding(1.dp)
                            .background(Color(0xFF0D0D0D), RoundedCornerShape(10.dp)),
                    )
                }
            }
            Spacer(Modifier.size(12.dp))
        }
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
