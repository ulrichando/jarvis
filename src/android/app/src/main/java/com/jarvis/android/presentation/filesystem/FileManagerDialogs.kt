package com.jarvis.android.presentation.filesystem

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.webkit.MimeTypeMap
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.OpenInNew
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.DriveFileRenameOutline
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Share
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.core.content.FileProvider
import com.jarvis.android.domain.model.FileItem
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Bottom-sheet context menu for a file manager item (MaterialFiles pattern).
 * Triggered by long-press.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FileContextSheet(
    item:         FileItem,
    onDismiss:    () -> Unit,
    onOpen:       () -> Unit,
    onRename:     () -> Unit,
    onDelete:     () -> Unit,
    onShare:      () -> Unit,
    onCopyPath:   () -> Unit,
    onProperties: () -> Unit,
) {
    val sheetState = rememberModalBottomSheetState()
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheetState) {
        Column(Modifier.fillMaxWidth().padding(bottom = 16.dp)) {
            Text(
                text     = item.name,
                style    = MaterialTheme.typography.titleMedium,
                modifier = Modifier.padding(horizontal = 20.dp, vertical = 4.dp),
            )
            Text(
                text       = item.path,
                style      = MaterialTheme.typography.bodySmall,
                color      = MaterialTheme.colorScheme.onSurfaceVariant,
                fontFamily = FontFamily.Monospace,
                modifier   = Modifier.padding(horizontal = 20.dp),
            )
            Spacer(Modifier.height(12.dp))
            HorizontalDivider()

            SheetAction(Icons.AutoMirrored.Filled.OpenInNew, "Open", onOpen)
            if (!item.isDirectory) {
                SheetAction(Icons.Default.Share, "Share", onShare)
            }
            SheetAction(Icons.Default.DriveFileRenameOutline, "Rename", onRename)
            SheetAction(Icons.Default.ContentCopy, "Copy path", onCopyPath)
            SheetAction(Icons.Default.Info, "Properties", onProperties)
            HorizontalDivider()
            SheetAction(Icons.Default.Delete, "Delete", onDelete, tintError = true)
        }
    }
}

@Composable
private fun SheetAction(
    icon:      ImageVector,
    label:     String,
    onClick:   () -> Unit,
    tintError: Boolean = false,
) {
    val color = if (tintError) MaterialTheme.colorScheme.error
                else           MaterialTheme.colorScheme.onSurface
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 20.dp, vertical = 14.dp),
        horizontalArrangement = Arrangement.spacedBy(16.dp),
        verticalAlignment     = Alignment.CenterVertically,
    ) {
        Icon(icon, null, tint = color, modifier = Modifier.size(22.dp))
        Text(label, style = MaterialTheme.typography.bodyLarge, color = color)
    }
}

@Composable
fun RenameDialog(
    item:      FileItem,
    onDismiss: () -> Unit,
    onConfirm: (String) -> Unit,
) {
    var name by remember(item.path) { mutableStateOf(item.name) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Rename") },
        text  = {
            OutlinedTextField(
                value         = name,
                onValueChange = { name = it },
                label         = { Text("New name") },
                singleLine    = true,
            )
        },
        confirmButton = {
            TextButton(
                onClick = { onConfirm(name); onDismiss() },
                enabled = name.isNotBlank() && name != item.name && !name.contains('/'),
            ) { Text("Rename") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Cancel") }
        },
    )
}

@Composable
fun DeleteConfirmDialog(
    item:      FileItem,
    onDismiss: () -> Unit,
    onConfirm: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (item.isDirectory) "Delete folder?" else "Delete file?") },
        text  = {
            Text(
                if (item.isDirectory)
                    "\"${item.name}\" and all of its contents will be removed. This can't be undone."
                else
                    "\"${item.name}\" will be removed. This can't be undone.",
            )
        },
        confirmButton = {
            TextButton(onClick = { onConfirm(); onDismiss() }) {
                Text("Delete", color = MaterialTheme.colorScheme.error)
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Cancel") }
        },
    )
}

@Composable
fun PropertiesDialog(
    item:      FileItem,
    onDismiss: () -> Unit,
) {
    val fmt = remember { SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(item.name) },
        text  = {
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                PropRow("Path",        item.path, mono = true)
                PropRow("Type",
                    when {
                        item.isSymlink   -> "Symbolic link"
                        item.isDirectory -> "Directory"
                        item.extension.isNotEmpty() -> "${item.extension.uppercase()} file"
                        else -> "File"
                    },
                )
                if (!item.isDirectory) PropRow("Size", formatBytes(item.sizeBytes))
                PropRow("Permissions", item.permissions, mono = true)
                PropRow("Owner",       "${item.owner}:${item.group}")
                PropRow("Modified",    fmt.format(Date(item.lastModified)))
                if (item.isHidden) PropRow("Hidden", "yes")
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) { Text("Close") }
        },
    )
}

@Composable
private fun PropRow(label: String, value: String, mono: Boolean = false) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(
            text  = label,
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.width(96.dp),
        )
        Text(
            text  = value,
            style = MaterialTheme.typography.bodySmall,
            fontFamily = if (mono) FontFamily.Monospace else null,
        )
    }
}

/**
 * Sort-options dropdown attached to the top bar. MaterialFiles pattern —
 * Name/Size/Modified/Type × ↑/↓, plus a Folders-first toggle.
 */
@Composable
fun SortMenu(
    expanded: Boolean,
    current:  SortMode,
    onDismiss:() -> Unit,
    onSelect: (SortMode) -> Unit,
) {
    val options = listOf(
        SortBy.NAME     to "Name",
        SortBy.SIZE     to "Size",
        SortBy.MODIFIED to "Modified",
        SortBy.TYPE     to "Type",
    )
    DropdownMenu(expanded = expanded, onDismissRequest = onDismiss) {
        options.forEach { (by, label) ->
            val mark = when {
                current.by != by -> "   "
                current.ascending -> " ↑"
                else              -> " ↓"
            }
            DropdownMenuItem(
                text    = { Text("$label$mark") },
                onClick = {
                    val newMode = if (current.by == by)
                        current.copy(ascending = !current.ascending)
                    else
                        current.copy(by = by, ascending = true)
                    onSelect(newMode)
                    onDismiss()
                },
            )
        }
        HorizontalDivider()
        DropdownMenuItem(
            text    = { Text(if (current.dirsFirst) "✓ Folders first" else "  Folders first") },
            onClick = {
                onSelect(current.copy(dirsFirst = !current.dirsFirst))
                onDismiss()
            },
        )
    }
}

// ── Share / clipboard helpers ─────────────────────────────────────────────────

fun shareFile(ctx: Context, item: FileItem) {
    if (item.isDirectory) return
    val file = File(item.path)
    val uri = runCatching {
        FileProvider.getUriForFile(ctx, "${ctx.packageName}.fileprovider", file)
    }.getOrNull() ?: return
    val ext  = item.extension.lowercase()
    val mime = MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext) ?: "*/*"
    val send = Intent(Intent.ACTION_SEND).apply {
        type = mime
        putExtra(Intent.EXTRA_STREAM, uri)
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }
    ctx.startActivity(Intent.createChooser(send, "Share ${item.name}").apply {
        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    })
}

fun copyToClipboard(ctx: Context, label: String, text: String) {
    val cm = ctx.getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager ?: return
    cm.setPrimaryClip(ClipData.newPlainText(label, text))
}

private fun formatBytes(bytes: Long): String = when {
    bytes >= 1_073_741_824 -> "%.2f GB (%,d bytes)".format(bytes / 1_073_741_824.0, bytes)
    bytes >= 1_048_576     -> "%.2f MB (%,d bytes)".format(bytes / 1_048_576.0, bytes)
    bytes >= 1_024         -> "%.1f KB (%,d bytes)".format(bytes / 1_024.0, bytes)
    else                   -> "$bytes bytes"
}
