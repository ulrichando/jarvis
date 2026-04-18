package com.jarvis.android.presentation.components

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.InsertDriveFile
import androidx.compose.material.icons.filled.Code
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.FolderOpen
import androidx.compose.material.icons.filled.Image
import androidx.compose.material.icons.filled.Link
import androidx.compose.material.icons.filled.MusicNote
import androidx.compose.material.icons.filled.VideoFile
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.jarvis.android.core.designsystem.JarvisTheme
import com.jarvis.android.domain.model.FileItem

/**
 * A single row in the file manager's directory listing.
 *
 * Tapping navigates into directories or opens files. Long-press (handled by
 * [FileManagerScreen]) triggers the context menu (rename, delete, share).
 */
@Composable
fun FileTreeItem(
    item:      FileItem,
    isExpanded:Boolean = false,
    onClick:   (FileItem) -> Unit,
    modifier:  Modifier = Modifier,
) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .clickable { onClick(item) }
            .padding(horizontal = 16.dp, vertical = 10.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
        verticalAlignment     = Alignment.CenterVertically,
    ) {
        // Icon
        Icon(
            imageVector = fileIcon(item, isExpanded),
            contentDescription = null,
            tint     = iconTint(item),
            modifier = Modifier.size(20.dp),
        )

        // Name + meta
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text     = item.name,
                style    = MaterialTheme.typography.bodyMedium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                color    = if (item.isHidden)
                    MaterialTheme.colorScheme.onSurface.copy(alpha = 0.5f)
                else
                    MaterialTheme.colorScheme.onSurface,
            )
            if (!item.isDirectory) {
                Text(
                    text  = formatSize(item.sizeBytes),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }

        // Permissions badge
        Text(
            text  = item.permissions.take(9),
            style = JarvisTheme.typography.filePerms,
            color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f),
        )
    }
}

@Composable
private fun fileIcon(item: FileItem, isExpanded: Boolean) = when {
    item.isSymlink  -> Icons.Default.Link
    item.isDirectory -> if (isExpanded) Icons.Default.FolderOpen else Icons.Default.Folder
    else -> when (item.extension.lowercase()) {
        "jpg", "jpeg", "png", "webp", "gif", "bmp" -> Icons.Default.Image
        "mp4", "mkv", "avi", "mov", "webm"          -> Icons.Default.VideoFile
        "mp3", "aac", "flac", "ogg", "wav"          -> Icons.Default.MusicNote
        "kt", "java", "py", "js", "ts", "c", "cpp",
        "h", "sh", "json", "xml", "yaml", "toml"    -> Icons.Default.Code
        else -> Icons.AutoMirrored.Filled.InsertDriveFile
    }
}

@Composable
private fun iconTint(item: FileItem) = when {
    item.isSymlink   -> MaterialTheme.colorScheme.tertiary
    item.isDirectory -> MaterialTheme.colorScheme.primary
    else             -> MaterialTheme.colorScheme.onSurfaceVariant
}

private fun formatSize(bytes: Long) = when {
    bytes >= 1_073_741_824 -> "${"%.1f".format(bytes / 1_073_741_824f)} GB"
    bytes >= 1_048_576     -> "${"%.1f".format(bytes / 1_048_576f)} MB"
    bytes >= 1_024         -> "${"%.0f".format(bytes / 1_024f)} KB"
    else                   -> "$bytes B"
}
