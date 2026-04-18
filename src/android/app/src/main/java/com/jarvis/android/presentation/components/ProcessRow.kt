package com.jarvis.android.presentation.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.jarvis.android.core.designsystem.JarvisTheme
import com.jarvis.android.domain.model.ProcessInfo

/**
 * A single row in the process list.
 *
 * Layout:
 *  [PID]  [Name (ellipsized)]  [User]  [RSS MB]  [CPU%]
 */
@Composable
fun ProcessRow(
    process:  ProcessInfo,
    modifier: Modifier = Modifier,
    onKill:   ((ProcessInfo) -> Unit)? = null,
) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 6.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        // PID
        Text(
            text  = process.pid.toString(),
            style = JarvisTheme.typography.filePerms,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.width(48.dp),
        )

        // Process name + user
        Column(modifier = Modifier.weight(1f).padding(horizontal = 8.dp)) {
            Text(
                text     = process.name,
                style    = MaterialTheme.typography.bodyMedium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                text  = process.user,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        // RSS
        Text(
            text  = formatRss(process.rssKb),
            style = JarvisTheme.typography.sensorValue,
            color = MaterialTheme.colorScheme.onSurface,
            modifier = Modifier.width(56.dp),
        )

        // CPU%
        if (process.cpuPercent > 0f) {
            Text(
                text  = "${process.cpuPercent.toInt()}%",
                style = JarvisTheme.typography.sensorValue,
                color = cpuColor(process.cpuPercent),
                modifier = Modifier.width(40.dp),
            )
        }
    }
}

private fun formatRss(rssKb: Long): String = when {
    rssKb >= 1_048_576 -> "${"%.1f".format(rssKb / 1_048_576f)} GB"
    rssKb >= 1_024     -> "${"%.0f".format(rssKb / 1_024f)} MB"
    else               -> "$rssKb KB"
}

@Composable
private fun cpuColor(pct: Float) = when {
    pct >= 50f -> MaterialTheme.colorScheme.error
    pct >= 20f -> MaterialTheme.colorScheme.tertiary
    else       -> MaterialTheme.colorScheme.onSurface
}
