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
import androidx.compose.material.icons.filled.FolderSpecial
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.LockOpen
import androidx.compose.material.icons.filled.Sort
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.jarvis.android.domain.model.FileItem
import com.jarvis.android.presentation.components.FileTreeItem

private enum class ActiveDialog { NONE, RENAME, DELETE, PROPERTIES }

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FileManagerScreen(
    onBack:    () -> Unit = {},
    viewModel: FileManagerViewModel = hiltViewModel(),
) {
    val state        by viewModel.uiState.collectAsState()
    val snackbar      = remember { SnackbarHostState() }
    var newDirName   by remember { mutableStateOf("") }
    val ctx           = LocalContext.current

    var sortMenuOpen   by remember { mutableStateOf(false) }
    var placesMenuOpen by remember { mutableStateOf(false) }
    var contextItem    by remember { mutableStateOf<FileItem?>(null) }
    var activeDialog   by remember { mutableStateOf(ActiveDialog.NONE) }

    val places = remember(ctx) { buildPlaces(ctx) }

    LaunchedEffect(state.error) {
        state.error?.let { snackbar.showSnackbar(it); viewModel.onIntent(FileManagerIntent.ClearError) }
    }

    // MANAGE_EXTERNAL_STORAGE runtime banner.
    val hasAllFilesAccess = remember(state.path) {
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.R)
            android.os.Environment.isExternalStorageManager()
        else true
    }
    if (!hasAllFilesAccess) {
        AllFilesAccessBanner(onGrant = {
            val intent = android.content.Intent(
                android.provider.Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                android.net.Uri.parse("package:${ctx.packageName}"),
            )
            runCatching { ctx.startActivity(intent) }.onFailure {
                ctx.startActivity(
                    android.content.Intent(android.provider.Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION),
                )
            }
        })
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
                    Box {
                        IconButton(onClick = { placesMenuOpen = true }) {
                            Icon(Icons.Default.FolderSpecial, "Places")
                        }
                        DropdownMenu(
                            expanded = placesMenuOpen,
                            onDismissRequest = { placesMenuOpen = false },
                        ) {
                            places.forEach { place ->
                                DropdownMenuItem(
                                    text = {
                                        Column {
                                            Text(place.label, style = MaterialTheme.typography.bodyMedium)
                                            Text(
                                                place.path,
                                                style = MaterialTheme.typography.labelSmall,
                                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                                fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace,
                                            )
                                        }
                                    },
                                    onClick = {
                                        viewModel.onIntent(FileManagerIntent.Navigate(place.path))
                                        placesMenuOpen = false
                                    },
                                )
                            }
                        }
                    }
                    Box {
                        IconButton(onClick = { sortMenuOpen = true }) {
                            Icon(Icons.Default.Sort, "Sort")
                        }
                        SortMenu(
                            expanded  = sortMenuOpen,
                            current   = state.sortMode,
                            onDismiss = { sortMenuOpen = false },
                            onSelect  = { viewModel.onIntent(FileManagerIntent.SetSort(it)) },
                        )
                    }
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
                            item         = item,
                            selected     = contextItem?.path == item.path,
                            onClick      = { viewModel.onIntent(FileManagerIntent.OpenFile(it)) },
                            onLongClick  = { contextItem = it },
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

    // Long-press context sheet and follow-up dialogs
    val ci = contextItem
    if (ci != null && activeDialog == ActiveDialog.NONE) {
        FileContextSheet(
            item         = ci,
            onDismiss    = { contextItem = null },
            onOpen       = {
                viewModel.onIntent(FileManagerIntent.OpenFile(ci))
                contextItem = null
            },
            onRename     = { activeDialog = ActiveDialog.RENAME },
            onDelete     = { activeDialog = ActiveDialog.DELETE },
            onShare      = {
                shareFile(ctx, ci)
                contextItem = null
            },
            onCopyPath   = {
                copyToClipboard(ctx, "Path", ci.path)
                contextItem = null
            },
            onProperties = { activeDialog = ActiveDialog.PROPERTIES },
        )
    }

    if (ci != null && activeDialog == ActiveDialog.RENAME) {
        RenameDialog(
            item      = ci,
            onDismiss = { activeDialog = ActiveDialog.NONE; contextItem = null },
            onConfirm = { newName ->
                viewModel.onIntent(FileManagerIntent.RenameItem(ci, newName))
            },
        )
    }
    if (ci != null && activeDialog == ActiveDialog.DELETE) {
        DeleteConfirmDialog(
            item      = ci,
            onDismiss = { activeDialog = ActiveDialog.NONE; contextItem = null },
            onConfirm = { viewModel.onIntent(FileManagerIntent.DeleteItem(ci)) },
        )
    }
    if (ci != null && activeDialog == ActiveDialog.PROPERTIES) {
        PropertiesDialog(
            item      = ci,
            onDismiss = { activeDialog = ActiveDialog.NONE; contextItem = null },
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

@Composable
private fun AllFilesAccessBanner(onGrant: () -> Unit) {
    androidx.compose.material3.Card(
        modifier = Modifier.fillMaxWidth().padding(12.dp),
        colors   = androidx.compose.material3.CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.errorContainer,
        ),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text  = "All Files Access is off",
                    style = MaterialTheme.typography.titleSmall,
                    color = MaterialTheme.colorScheme.onErrorContainer,
                )
                Text(
                    text  = "Android won't let Jarvis see /sdcard or anywhere else until you grant it. Tap Grant, then flip the Jarvis toggle on.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onErrorContainer,
                )
            }
            TextButton(onClick = onGrant) { Text("Grant") }
        }
    }
}

/**
 * Quick-access shortcut. The menu is populated from [buildPlaces] which
 * probes for the conventional Android storage paths (Downloads, DCIM, etc.)
 * and adds internal-storage roots (app sandbox, /data, /system, /) so the
 * file manager can reach anything, not just /sdcard.
 */
private data class Place(val label: String, val path: String)

private fun buildPlaces(ctx: android.content.Context): List<Place> {
    val list = mutableListOf<Place>()
    // External (user-visible) storage
    val ext = android.os.Environment.getExternalStorageDirectory().absolutePath
    list += Place("Home",        ext)
    list += Place("Downloads",   "$ext/Download")
    list += Place("Pictures",    "$ext/Pictures")
    list += Place("DCIM",        "$ext/DCIM")
    list += Place("Movies",      "$ext/Movies")
    list += Place("Music",       "$ext/Music")
    list += Place("Documents",   "$ext/Documents")
    // All mounted storage volumes (SD card, USB OTG)
    list += Place("Storage",     "/storage")
    // App's own sandbox — always accessible without extra permissions
    list += Place("App storage", ctx.filesDir.absolutePath)
    list += Place("App cache",   ctx.cacheDir.absolutePath)
    // System roots — readable bits work without root; full access needs
    // the root-mode toggle (top-right lock icon) on rooted devices.
    list += Place("Data",        "/data")
    list += Place("System",      "/system")
    list += Place("Root",        "/")
    return list.filter { java.io.File(it.path).exists() || it.path == "/" }
}
