package com.jarvis.android.presentation.filesystem

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.LockOpen
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.jarvis.android.presentation.components.FileTreeItem

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FileManagerScreen(
    onBack:    () -> Unit = {},
    viewModel: FileManagerViewModel = hiltViewModel(),
) {
    val state        by viewModel.uiState.collectAsState()
    val snackbar      = remember { SnackbarHostState() }
    var newDirName   by remember { mutableStateOf("") }

    LaunchedEffect(state.error) {
        state.error?.let { snackbar.showSnackbar(it); viewModel.onIntent(FileManagerIntent.ClearError) }
    }

    // File viewer overlay
    if (state.fileContent != null) {
        FileViewerScreen(
            path    = state.selectedItem?.path ?: "",
            content = state.fileContent ?: "",
            onBack  = { viewModel.onIntent(FileManagerIntent.CloseFile) },
        )
        return
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            TopAppBar(
                title = {
                    // Breadcrumb row
                    Row(
                        modifier = Modifier.horizontalScroll(rememberScrollState()),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        state.breadcrumbs.forEachIndexed { i, crumb ->
                            val label = if (crumb == "/") "/" else crumb.substringAfterLast('/')
                            TextButton(onClick = { viewModel.onIntent(FileManagerIntent.Navigate(crumb)) }) {
                                Text(
                                    text  = label,
                                    style = MaterialTheme.typography.bodySmall,
                                    color = if (i == state.breadcrumbs.lastIndex)
                                        MaterialTheme.colorScheme.primary
                                    else
                                        MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                            if (i < state.breadcrumbs.lastIndex) {
                                Text("/", style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                    }
                },
                navigationIcon = {
                    IconButton(onClick = {
                        if (state.breadcrumbs.size > 1) viewModel.onIntent(FileManagerIntent.NavigateUp)
                        else onBack()
                    }) { Icon(Icons.Default.ArrowBack, "Up") }
                },
                actions = {
                    IconButton(onClick = { viewModel.onIntent(FileManagerIntent.ToggleRoot) }) {
                        Icon(
                            if (state.isRootMode) Icons.Default.Lock else Icons.Default.LockOpen,
                            if (state.isRootMode) "Root mode on" else "Root mode off",
                            tint = if (state.isRootMode) MaterialTheme.colorScheme.primary
                                   else MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                },
            )
        },
        floatingActionButton = {
            FloatingActionButton(onClick = { viewModel.onIntent(FileManagerIntent.ShowNewDirDialog) }) {
                Icon(Icons.Default.Add, "New directory")
            }
        },
    ) { padding ->
        Box(Modifier.padding(padding).fillMaxSize()) {
            if (state.isLoading) {
                CircularProgressIndicator(Modifier.align(Alignment.Center))
            } else {
                LazyColumn(Modifier.fillMaxSize()) {
                    items(state.items, key = { it.path }) { item ->
                        FileTreeItem(
                            item    = item,
                            onClick = { viewModel.onIntent(FileManagerIntent.OpenFile(it)) },
                        )
                        HorizontalDivider(thickness = 0.5.dp, color = MaterialTheme.colorScheme.outlineVariant)
                    }
                }
            }
        }
    }

    // New directory dialog
    if (state.showNewDirDialog) {
        AlertDialog(
            onDismissRequest = { viewModel.onIntent(FileManagerIntent.DismissNewDirDialog) },
            title = { Text("New Directory") },
            text  = {
                OutlinedTextField(
                    value         = newDirName,
                    onValueChange = { newDirName = it },
                    label         = { Text("Directory name") },
                    singleLine    = true,
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    if (newDirName.isNotBlank()) {
                        viewModel.onIntent(FileManagerIntent.CreateDirectory(newDirName))
                        newDirName = ""
                    }
                }) { Text("Create") }
            },
            dismissButton = {
                TextButton(onClick = { viewModel.onIntent(FileManagerIntent.DismissNewDirDialog) }) {
                    Text("Cancel")
                }
            },
        )
    }
}

@Composable
private fun FileViewerScreen(path: String, content: String, onBack: () -> Unit) {
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onBack) { Icon(Icons.Default.ArrowBack, "Back") }
            Text(path.substringAfterLast('/'), style = MaterialTheme.typography.titleSmall,
                modifier = Modifier.weight(1f))
        }
        HorizontalDivider()
        LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
            val lines = content.lines()
            items(lines.size) { i ->
                Row(Modifier.fillMaxWidth().padding(vertical = 1.dp)) {
                    Text(
                        text  = "%4d".format(i + 1),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.5f),
                        modifier = Modifier.padding(end = 12.dp),
                    )
                    Text(
                        text  = lines[i],
                        style = MaterialTheme.typography.bodySmall,
                        fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace,
                    )
                }
            }
        }
    }
}
