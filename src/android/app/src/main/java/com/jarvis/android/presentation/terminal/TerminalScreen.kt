package com.jarvis.android.presentation.terminal

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.horizontalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.jarvis.android.core.designsystem.JarvisPalette
import com.jarvis.android.presentation.components.TerminalView
import com.jarvis.android.system.terminal.TerminalGridSnapshot

/**
 * Full-screen PTY terminal with a scrollable session tab bar at the top.
 *
 * Layout:
 *   [← back] [tab1] [tab2] [+]   ← tab strip
 *   ┌───────────────────────────┐
 *   │  TerminalView (Canvas)    │
 *   └───────────────────────────┘
 */
@Composable
fun TerminalScreen(
    onBack:    () -> Unit = {},
    viewModel: TerminalViewModel = hiltViewModel(),
) {
    val state by viewModel.uiState.collectAsState()

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(JarvisPalette.TerminalBg)
            .statusBarsPadding(),
    ) {
        // Tab strip
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .background(MaterialTheme.colorScheme.surfaceContainerLow)
                .horizontalScroll(rememberScrollState())
                .padding(horizontal = 4.dp, vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(onClick = onBack, modifier = Modifier.size(36.dp)) {
                Icon(
                    Icons.Default.ArrowBack, "Back",
                    tint = MaterialTheme.colorScheme.onSurface,
                )
            }

            state.sessions.forEach { session ->
                val isActive = session.id == state.activeSessionId
                FilterChip(
                    selected  = isActive,
                    onClick   = { viewModel.onIntent(TerminalIntent.SelectSession(session.id)) },
                    label     = {
                        Text(
                            text  = session.name,
                            style = MaterialTheme.typography.labelSmall,
                            maxLines = 1,
                        )
                    },
                    trailingIcon = {
                        IconButton(
                            onClick  = { viewModel.onIntent(TerminalIntent.KillSession(session.id)) },
                            modifier = Modifier.size(16.dp),
                        ) {
                            Icon(Icons.Default.Close, "Close", modifier = Modifier.size(12.dp))
                        }
                    },
                    colors = FilterChipDefaults.filterChipColors(
                        selectedContainerColor = JarvisPalette.GoldPrimary.copy(alpha = 0.2f),
                    ),
                    modifier = Modifier.padding(end = 4.dp),
                )
            }

            // New session button
            IconButton(
                onClick  = { viewModel.onIntent(TerminalIntent.NewSession) },
                enabled  = !state.isCreating,
                modifier = Modifier.size(36.dp),
            ) {
                if (state.isCreating) {
                    CircularProgressIndicator(
                        modifier    = Modifier.size(16.dp),
                        strokeWidth = 2.dp,
                    )
                } else {
                    Icon(Icons.Default.Add, "New session", tint = MaterialTheme.colorScheme.primary)
                }
            }
        }

        // Terminal canvas
        val snapshot: TerminalGridSnapshot = state.gridSnapshot
            ?: TerminalGridSnapshot(
                grid = ByteArray(0), rows = 24, cols = 80,
                cursorRow = 0, cursorCol = 0, cursorVisible = false,
                title = "", scrollbackSize = 0,
            )

        TerminalView(
            snapshot = snapshot,
            onInput  = { text -> viewModel.onIntent(TerminalIntent.Write(text)) },
            onResize = { rows, cols -> viewModel.onIntent(TerminalIntent.Resize(rows, cols)) },
            modifier = Modifier.fillMaxSize(),
        )
    }
}
