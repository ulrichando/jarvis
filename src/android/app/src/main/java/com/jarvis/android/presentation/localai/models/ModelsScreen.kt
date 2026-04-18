package com.jarvis.android.presentation.localai.models

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.Memory
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MenuAnchorType
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import com.jarvis.android.core.designsystem.JarvisPalette
import com.jarvis.android.core.designsystem.JarvisTheme
import com.jarvis.android.domain.model.DownloadState
import com.jarvis.android.domain.model.ModelBackend
import com.jarvis.android.domain.model.ModelCapability
import com.jarvis.android.domain.model.ModelEntry
import com.jarvis.android.domain.model.RoutingMode
import com.jarvis.android.domain.model.isOnDevice
import com.jarvis.android.domain.model.ramLabel
import com.jarvis.android.domain.model.sizeFormatted

@Composable
fun ModelsScreen(viewModel: ModelsViewModel = hiltViewModel()) {
    val state    by viewModel.uiState.collectAsState()
    val snackbar  = remember { SnackbarHostState() }

    LaunchedEffect(state.toast) {
        state.toast?.let { snackbar.showSnackbar(it); viewModel.onToastShown() }
    }

    Scaffold(
        containerColor = JarvisPalette.ObsidianBlack,
        snackbarHost   = { SnackbarHost(snackbar) },
        floatingActionButton = {
            FloatingActionButton(
                onClick            = viewModel::onShowImportDialog,
                containerColor     = JarvisPalette.GoldPrimary,
                contentColor       = JarvisPalette.TextOnGold,
            ) { Icon(Icons.Default.Add, "Import custom model") }
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .padding(padding)
                .fillMaxSize()
        ) {
            // ── Routing mode chips ────────────────────────────────────────────
            RoutingModeRow(
                current  = state.routingMode,
                onChange = viewModel::onRoutingModeChange,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp),
            )

            // ── Storage bar ───────────────────────────────────────────────────
            if (state.storageUsedBytes > 0L) {
                StorageBar(
                    usedBytes = state.storageUsedBytes,
                    modifier  = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 4.dp),
                )
            }

            // ── Load progress ─────────────────────────────────────────────────
            if (state.loadingModelId != null) {
                LoadProgressBanner(
                    modelId  = state.loadingModelId!!,
                    progress = state.loadProgress,
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 4.dp),
                )
            }

            // ── Model list ────────────────────────────────────────────────────
            if (state.isRefreshing && state.models.isEmpty()) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator(color = JarvisPalette.GoldPrimary)
                }
            } else {
                LazyColumn(
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                    contentPadding      = androidx.compose.foundation.layout.PaddingValues(
                        start = 16.dp, end = 16.dp, top = 8.dp, bottom = 96.dp,
                    ),
                ) {
                    items(state.models, key = { it.id }) { model ->
                        ModelCard(
                            model          = model,
                            isLoaded       = model.id == state.loadedModelId,
                            isBeingLoaded  = model.id == state.loadingModelId,
                            onDownload     = { viewModel.onDownload(model.id) },
                            onCancelDownload = { viewModel.onCancelDownload(model.id) },
                            onDelete       = { viewModel.onDelete(model.id) },
                            onLoad         = { viewModel.onLoad(model.id) },
                            onUnload       = { viewModel.onUnload(model.id) },
                        )
                    }
                }
            }
        }
    }

    // ── Import dialog ─────────────────────────────────────────────────────────
    if (state.showImportDialog) {
        ImportCustomModelDialog(
            onDismiss = viewModel::onDismissImportDialog,
            onImport  = viewModel::onImportCustom,
        )
    }
}

// ── Routing mode row ──────────────────────────────────────────────────────────

@Composable
private fun RoutingModeRow(
    current:  RoutingMode,
    onChange: (RoutingMode) -> Unit,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier            = modifier,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        RoutingMode.entries.forEach { mode ->
            val selected = mode == current
            Surface(
                onClick       = { onChange(mode) },
                shape         = RoundedCornerShape(20.dp),
                color         = if (selected) JarvisPalette.GoldDim else JarvisPalette.SurfaceElevated,
                border        = if (selected)
                    androidx.compose.foundation.BorderStroke(1.dp, JarvisPalette.GoldPrimary)
                else null,
            ) {
                Text(
                    text     = mode.label,
                    color    = if (selected) JarvisPalette.GoldGlow else JarvisPalette.TextSecondary,
                    fontSize = 12.sp,
                    fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
                )
            }
        }
    }
}

// ── Storage bar ───────────────────────────────────────────────────────────────

@Composable
private fun StorageBar(usedBytes: Long, modifier: Modifier = Modifier) {
    val label = when {
        usedBytes >= 1_073_741_824L -> "%.1f GB used".format(usedBytes / 1_073_741_824.0)
        usedBytes >= 1_048_576L     -> "%.0f MB used".format(usedBytes / 1_048_576.0)
        else                        -> "$usedBytes B used"
    }
    Row(modifier, verticalAlignment = Alignment.CenterVertically) {
        Icon(Icons.Default.Memory, null, tint = JarvisPalette.GoldMuted, modifier = Modifier.size(14.dp))
        Spacer(Modifier.width(6.dp))
        Text(label, color = JarvisPalette.TextSecondary, fontSize = 11.sp)
    }
}

// ── Load progress banner ──────────────────────────────────────────────────────

@Composable
private fun LoadProgressBanner(modelId: String, progress: String, modifier: Modifier = Modifier) {
    Column(modifier
        .clip(RoundedCornerShape(8.dp))
        .background(JarvisPalette.GoldDim)
        .padding(12.dp)
    ) {
        Text(progress, color = JarvisPalette.GoldGlow, fontSize = 12.sp)
        Spacer(Modifier.height(6.dp))
        LinearProgressIndicator(
            modifier          = Modifier.fillMaxWidth(),
            color             = JarvisPalette.GoldPrimary,
            trackColor        = JarvisPalette.GoldBorder,
        )
    }
}

// ── Model card ────────────────────────────────────────────────────────────────

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ModelCard(
    model:           ModelEntry,
    isLoaded:        Boolean,
    isBeingLoaded:   Boolean,
    onDownload:      () -> Unit,
    onCancelDownload: () -> Unit,
    onDelete:        () -> Unit,
    onLoad:          () -> Unit,
    onUnload:        () -> Unit,
) {
    val borderColor = when {
        isLoaded       -> JarvisPalette.GoldPrimary
        model.isOnDevice -> JarvisPalette.SuccessGreen.copy(alpha = 0.4f)
        else           -> JarvisPalette.GoldBorder
    }

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(JarvisPalette.SurfaceElevated)
            .border(1.dp, borderColor, RoundedCornerShape(12.dp))
            .padding(14.dp)
            .animateContentSize()
    ) {
        // ── Header row ────────────────────────────────────────────────────────
        Row(verticalAlignment = Alignment.Top) {
            Column(Modifier.weight(1f)) {
                Text(
                    text       = model.name,
                    color      = JarvisPalette.TextPrimary,
                    fontSize   = 14.sp,
                    fontWeight = FontWeight.SemiBold,
                )
                Spacer(Modifier.height(2.dp))
                Text(
                    text     = "${model.sizeFormatted}  ·  ${model.ramLabel} RAM  ·  ${model.backend.label}",
                    color    = JarvisPalette.TextSecondary,
                    fontSize = 11.sp,
                )
            }

            // State badge
            AnimatedContent(targetState = model.downloadState, label = "state") { state ->
                when (state) {
                    is DownloadState.Loaded       -> StateBadge("LOADED",     JarvisPalette.GoldPrimary)
                    is DownloadState.Downloaded   -> StateBadge("ON DEVICE",  JarvisPalette.SuccessGreen)
                    is DownloadState.Downloading  -> StateBadge("${(state.progress * 100).toInt()}%", JarvisPalette.WarningAmber)
                    is DownloadState.Failed       -> StateBadge("FAILED",     JarvisPalette.ErrorRed)
                    is DownloadState.NotDownloaded -> StateBadge("CLOUD",     JarvisPalette.TextDisabled)
                }
            }
        }

        // ── Description ───────────────────────────────────────────────────────
        if (model.description.isNotBlank()) {
            Spacer(Modifier.height(6.dp))
            Text(
                text     = model.description,
                color    = JarvisPalette.TextSecondary,
                fontSize = 11.sp,
                lineHeight = 15.sp,
            )
        }

        // ── Capability chips ──────────────────────────────────────────────────
        Spacer(Modifier.height(8.dp))
        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            model.capabilities.forEach { cap ->
                CapabilityChip(cap)
            }
            if (model.contextLength > 2048) {
                ContextChip(model.contextLength)
            }
        }

        // ── Download progress bar ─────────────────────────────────────────────
        val downloading = model.downloadState as? DownloadState.Downloading
        if (downloading != null) {
            Spacer(Modifier.height(8.dp))
            LinearProgressIndicator(
                progress          = { downloading.progress },
                modifier          = Modifier.fillMaxWidth(),
                color             = JarvisPalette.GoldPrimary,
                trackColor        = JarvisPalette.GoldBorder,
            )
        }

        // ── Action buttons ────────────────────────────────────────────────────
        Spacer(Modifier.height(10.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            when (model.downloadState) {
                is DownloadState.NotDownloaded, is DownloadState.Failed -> {
                    ActionButton("Download", JarvisPalette.GoldPrimary, JarvisPalette.TextOnGold, onDownload) {
                        Icon(Icons.Default.Download, null, modifier = Modifier.size(14.dp))
                    }
                }
                is DownloadState.Downloading -> {
                    ActionButton("Cancel", JarvisPalette.ErrorContainer, JarvisPalette.ErrorRed, onCancelDownload) {
                        Icon(Icons.Default.Close, null, modifier = Modifier.size(14.dp))
                    }
                }
                is DownloadState.Downloaded -> {
                    if (isLoaded) {
                        ActionButton("Unload", JarvisPalette.GoldDim, JarvisPalette.GoldPrimary, onUnload) {
                            Icon(Icons.Default.Check, null, modifier = Modifier.size(14.dp))
                        }
                    } else {
                        ActionButton(
                            if (isBeingLoaded) "Loading…" else "Load",
                            JarvisPalette.GoldPrimary, JarvisPalette.TextOnGold,
                            if (isBeingLoaded) ({}) else onLoad,
                        ) {
                            if (isBeingLoaded)
                                CircularProgressIndicator(
                                    modifier = Modifier.size(14.dp),
                                    strokeWidth = 2.dp,
                                    color = JarvisPalette.TextOnGold,
                                )
                            else
                                Icon(Icons.Default.Memory, null, modifier = Modifier.size(14.dp))
                        }
                        Spacer(Modifier.width(4.dp))
                        IconButton(onClick = onDelete, modifier = Modifier.size(32.dp)) {
                            Icon(
                                Icons.Default.Delete, "Delete",
                                tint     = JarvisPalette.ErrorRed,
                                modifier = Modifier.size(16.dp),
                            )
                        }
                    }
                }
                is DownloadState.Loaded -> {
                    ActionButton("Unload", JarvisPalette.GoldDim, JarvisPalette.GoldPrimary, onUnload) {
                        Icon(Icons.Default.Check, null, modifier = Modifier.size(14.dp))
                    }
                }
            }
        }
    }
}

// ── Small components ──────────────────────────────────────────────────────────

@Composable
private fun StateBadge(label: String, color: androidx.compose.ui.graphics.Color) {
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(4.dp))
            .background(color.copy(alpha = 0.15f))
            .padding(horizontal = 6.dp, vertical = 2.dp)
    ) {
        Text(label, color = color, fontSize = 10.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.5.sp)
    }
}

@Composable
private fun CapabilityChip(cap: ModelCapability) {
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(4.dp))
            .background(JarvisPalette.SurfaceOverlay)
            .border(1.dp, JarvisPalette.GoldBorder, RoundedCornerShape(4.dp))
            .padding(horizontal = 6.dp, vertical = 2.dp)
    ) {
        Text(cap.label, color = JarvisPalette.TextSecondary, fontSize = 10.sp)
    }
}

@Composable
private fun ContextChip(contextLength: Int) {
    val label = when {
        contextLength >= 131_072 -> "128K ctx"
        contextLength >= 65_536  -> "64K ctx"
        contextLength >= 32_768  -> "32K ctx"
        contextLength >= 16_384  -> "16K ctx"
        contextLength >= 8_192   -> "8K ctx"
        else                     -> "${contextLength / 1024}K ctx"
    }
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(4.dp))
            .background(JarvisPalette.GoldDim.copy(alpha = 0.5f))
            .padding(horizontal = 6.dp, vertical = 2.dp)
    ) {
        Text(label, color = JarvisPalette.GoldMuted, fontSize = 10.sp)
    }
}

@Composable
private fun ActionButton(
    label:    String,
    bgColor:  androidx.compose.ui.graphics.Color,
    fgColor:  androidx.compose.ui.graphics.Color,
    onClick:  () -> Unit,
    icon:     @Composable () -> Unit = {},
) {
    FilledTonalButton(
        onClick  = onClick,
        colors   = ButtonDefaults.filledTonalButtonColors(
            containerColor = bgColor,
            contentColor   = fgColor,
        ),
        shape    = RoundedCornerShape(8.dp),
        modifier = Modifier.height(32.dp),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 12.dp, vertical = 0.dp),
    ) {
        icon()
        Spacer(Modifier.width(4.dp))
        Text(label, fontSize = 12.sp, fontWeight = FontWeight.Medium)
    }
}

// ── Import dialog ─────────────────────────────────────────────────────────────

@Composable
private fun ImportCustomModelDialog(
    onDismiss: () -> Unit,
    onImport:  (name: String, url: String, backend: ModelBackend) -> Unit,
) {
    var name     by rememberSaveable { mutableStateOf("") }
    var url      by rememberSaveable { mutableStateOf("") }
    var backend  by rememberSaveable { mutableStateOf(ModelBackend.LLAMACPP) }
    var expanded by remember { mutableStateOf(false) }

    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor   = JarvisPalette.SurfaceDark,
        title = {
            Text("Import Custom Model", color = JarvisPalette.GoldGlow, fontWeight = FontWeight.SemiBold)
        },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(
                    value          = name,
                    onValueChange  = { name = it },
                    label          = { Text("Model name") },
                    singleLine     = true,
                    modifier       = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value          = url,
                    onValueChange  = { url = it },
                    label          = { Text("Download URL (.gguf / .task)") },
                    singleLine     = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                    modifier       = Modifier.fillMaxWidth(),
                )
                ExposedDropdownMenuBox(expanded = expanded, onExpandedChange = { expanded = it }) {
                    OutlinedTextField(
                        value         = backend.label,
                        onValueChange = {},
                        readOnly      = true,
                        label         = { Text("Backend") },
                        trailingIcon  = { ExposedDropdownMenuDefaults.TrailingIcon(expanded) },
                        modifier      = Modifier.fillMaxWidth().menuAnchor(MenuAnchorType.PrimaryEditable),
                    )
                    ExposedDropdownMenu(
                        expanded        = expanded,
                        onDismissRequest = { expanded = false },
                        containerColor  = JarvisPalette.SurfaceElevated,
                    ) {
                        ModelBackend.entries.forEach { b ->
                            DropdownMenuItem(
                                text    = { Text(b.label, color = JarvisPalette.TextPrimary) },
                                onClick = { backend = b; expanded = false },
                            )
                        }
                    }
                }
            }
        },
        confirmButton = {
            TextButton(
                onClick  = { if (name.isNotBlank() && url.isNotBlank()) onImport(name, url, backend) },
                enabled  = name.isNotBlank() && url.isNotBlank(),
            ) { Text("Import", color = JarvisPalette.GoldPrimary) }
        },
        dismissButton = {
            TextButton(onDismiss) { Text("Cancel", color = JarvisPalette.TextSecondary) }
        },
    )
}
