package com.jarvis.android.presentation.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.jarvis.android.system.permissions.PermissionEntry
import com.jarvis.android.system.permissions.PermissionStatus
import com.jarvis.android.system.permissions.PermissionTier

/**
 * A single row in the permission matrix screen.
 *
 * Shows the permission name, a one-line description, a status badge
 * (green check / red X / root lock), and a "Grant" button when not yet granted.
 */
@Composable
fun PermissionRow(
    entry:    PermissionEntry,
    onGrant:  (PermissionEntry) -> Unit,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 8.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        // Status icon
        StatusIcon(entry.status, entry.tier, Modifier.padding(end = 12.dp))

        // Text
        Column(modifier = Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text  = entry.displayName,
                    style = MaterialTheme.typography.bodyMedium,
                )
                if (entry.isRequired) {
                    Text(
                        text     = " *",
                        style    = MaterialTheme.typography.bodyMedium,
                        color    = MaterialTheme.colorScheme.error,
                    )
                }
            }
            Text(
                text  = entry.description,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        // Grant button — only when denied
        if (entry.status != PermissionStatus.GRANTED) {
            Button(
                onClick = { onGrant(entry) },
                modifier = Modifier.padding(start = 8.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = if (entry.isRequired)
                        MaterialTheme.colorScheme.error
                    else
                        MaterialTheme.colorScheme.primary,
                ),
            ) {
                Text("Grant", style = MaterialTheme.typography.labelSmall)
            }
        }
    }
}

@Composable
private fun StatusIcon(
    status: PermissionStatus,
    tier:   PermissionTier,
    modifier: Modifier = Modifier,
) {
    when {
        status == PermissionStatus.GRANTED -> Icon(
            imageVector = Icons.Default.Check,
            contentDescription = "Granted",
            tint     = MaterialTheme.colorScheme.primary,
            modifier = modifier.size(20.dp),
        )
        tier == PermissionTier.ROOT -> Icon(
            imageVector = Icons.Default.Lock,
            contentDescription = "Root required",
            tint     = MaterialTheme.colorScheme.tertiary,
            modifier = modifier.size(20.dp),
        )
        else -> Icon(
            imageVector = Icons.Default.Close,
            contentDescription = "Denied",
            tint     = MaterialTheme.colorScheme.error,
            modifier = modifier.size(20.dp),
        )
    }
}
