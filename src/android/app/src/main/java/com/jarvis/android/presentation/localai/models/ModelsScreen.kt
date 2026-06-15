package com.jarvis.android.presentation.localai.models

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import androidx.compose.ui.graphics.Color
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

            // ── Dismiss-all-errors banner ─────────────────────────────────────
            // Each failed card already has its own "Dismiss" link, but when
            // several downloads fail together (common after a disk-full event)
            // clearing them one by one is tedious. This shortcut resets every
            // Failed card in one tap.
            val failedCount = state.models.count { it.downloadState is DownloadState.Failed }
            if (failedCount > 0) {
                DismissAllErrorsBanner(
                    count    = failedCount,
                    onClick  = viewModel::onDismissAllErrors,
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
                // Filter the catalog by the selected routing mode so the list
                // reflects the mental model of each chip:
                //   Local  → things that live on THIS device already
                //   Cloud  → things that DON'T live on this device, i.e. the
                //            download menu
                //   Auto / Hybrid → everything (no filter)
                val filtered = when (state.routingMode) {
                    RoutingMode.LOCAL  -> state.models.filter {
                        it.downloadState is DownloadState.Downloaded ||
                        it.downloadState is DownloadState.Loaded     ||
                        it.downloadState is DownloadState.Downloading
                    }
                    RoutingMode.CLOUD  -> state.models.filter {
                        it.downloadState is DownloadState.NotDownloaded ||
                        it.downloadState is DownloadState.Failed
                    }
                    else -> state.models
                }

                // Nothing downloaded yet → surface a prominent "get started"
                // hero so the path "pick a Gemma → tap Download → run it on
                // your phone" is obvious, matching Google AI Edge's first-run
                // funnel. Only shown under Cloud / Auto / Hybrid — the Local
                // filter by definition has nothing to recommend.
                val nothingDownloaded = state.routingMode != RoutingMode.LOCAL &&
                    state.models.all { it.downloadState !is DownloadState.Downloaded } &&
                    state.models.isNotEmpty()

                LazyColumn(
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                    contentPadding      = androidx.compose.foundation.layout.PaddingValues(
                        start = 16.dp, end = 16.dp, top = 8.dp, bottom = 96.dp,
                    ),
                ) {
                    if (nothingDownloaded) {
                        item(key = "on_device_hero") {
                            OnDeviceHeroCard(
                                recommended = pickRecommendedModel(state.models),
                                onDownload  = { id -> viewModel.onDownload(id) },
                                modifier    = Modifier.fillMaxWidth(),
                            )
                        }
                    }

                    // Empty-state message when the filter hides every card.
                    if (filtered.isEmpty()) {
                        item(key = "filter_empty") {
                            EmptyFilterCard(
                                mode     = state.routingMode,
                                modifier = Modifier.fillMaxWidth(),
                            )
                        }
                    }

                    items(filtered, key = { it.id }) { model ->
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

// ── Dismiss-all-errors banner ─────────────────────────────────────────────────

@Composable
private fun DismissAllErrorsBanner(
    count:    Int,
    onClick:  () -> Unit,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier = modifier
            .clip(RoundedCornerShape(8.dp))
            .background(JarvisPalette.ErrorRed.copy(alpha = 0.15f))
            .border(1.dp, JarvisPalette.ErrorRed.copy(alpha = 0.4f), RoundedCornerShape(8.dp))
            .padding(horizontal = 12.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text     = "$count download${if (count == 1) "" else "s"} failed",
            color    = JarvisPalette.ErrorRed,
            fontSize = 12.sp,
            modifier = Modifier.weight(1f),
        )
        TextButton(
            onClick = onClick,
            contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 12.dp, vertical = 4.dp),
        ) {
            Text(
                text       = "Dismiss all",
                color      = JarvisPalette.GoldPrimary,
                fontSize   = 12.sp,
                fontWeight = FontWeight.Medium,
            )
        }
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

        // ── Failure reason ────────────────────────────────────────────────────
        //
        // Without this the user just sees a "FAILED" badge and is left
        // guessing — common causes are 403 from HuggingFace (auth), 404
        // (moved URL), or out-of-space on /data/media. Surfacing the actual
        // reason from the DB lets them act on it, and the 'Dismiss' link
        // resets the card back to NotDownloaded so the error goes away
        // without requiring a retry.
        val failed = model.downloadState as? DownloadState.Failed
        if (failed != null) {
            Spacer(Modifier.height(8.dp))
            Row(
                verticalAlignment = Alignment.Top,
                modifier          = Modifier.fillMaxWidth(),
            ) {
                Text(
                    text       = "Error: ${failed.reason}",
                    color      = JarvisPalette.ErrorRed,
                    fontSize   = 11.sp,
                    lineHeight = 15.sp,
                    modifier   = Modifier.weight(1f),
                )
                Spacer(Modifier.width(8.dp))
                Text(
                    text     = "Dismiss",
                    color    = JarvisPalette.GoldPrimary,
                    fontSize = 11.sp,
                    fontWeight = FontWeight.Medium,
                    modifier = Modifier
                        .clickable(onClick = onDelete)
                        .padding(vertical = 2.dp, horizontal = 4.dp),
                )
            }
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
                    label          = { Text("Download URL (.gguf)") },
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
                        // MediaPipe is hidden — the .task runtime (libllm_inference_engine_jni.so)
                        // crashes in its drishti thread on Samsung devices and is no longer
                        // supported. The catalog and custom-import flow are both GGUF-only.
                        ModelBackend.entries
                            .filter { it != ModelBackend.MEDIAPIPE }
                            .forEach { b ->
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

// ── On-device hero ────────────────────────────────────────────────────────────

/**
 * First-run hero card shown at the top of the models list when the user has
 * nothing downloaded yet. Models the same pattern as Google AI Edge's
 * "Download Gemini Nano to run AI on-device" prompt: a short pitch, a hero
 * model recommendation, and a one-tap CTA that starts the download.
 *
 * Falls back gracefully when the catalog has no Gemma family at all — the
 * card still explains on-device AI and points the user at the list below.
 */
@Composable
private fun OnDeviceHeroCard(
    recommended: ModelEntry?,
    onDownload:  (String) -> Unit,
    modifier:    Modifier = Modifier,
) {
    val accent = JarvisPalette.GoldPrimary   // now blue — see JarvisPalette comment

    Column(
        modifier = modifier
            .background(
                color = accent.copy(alpha = 0.08f),
                shape = RoundedCornerShape(16.dp),
            )
            .border(
                width = 1.dp,
                color = accent.copy(alpha = 0.35f),
                shape = RoundedCornerShape(16.dp),
            )
            .padding(16.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(
                modifier = Modifier
                    .size(36.dp)
                    .background(accent.copy(alpha = 0.14f), RoundedCornerShape(10.dp)),
                contentAlignment = Alignment.Center,
            ) {
                Icon(
                    imageVector        = Icons.Default.Memory,
                    contentDescription = null,
                    tint               = accent,
                    modifier           = Modifier.size(20.dp),
                )
            }
            Spacer(Modifier.width(10.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text     = "Run AI on this device",
                    color    = JarvisPalette.TextPrimary,
                    fontSize = 15.sp,
                    fontWeight = FontWeight.SemiBold,
                )
                Text(
                    text     = "Download a small GGUF model and chat offline. Runs locally via llama.cpp.",
                    color    = JarvisPalette.TextSecondary,
                    fontSize = 12.sp,
                )
            }
        }

        if (recommended != null) {
            Spacer(Modifier.height(12.dp))
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(
                        color = JarvisPalette.SurfaceElevated,
                        shape = RoundedCornerShape(12.dp),
                    )
                    .padding(horizontal = 12.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text       = "Recommended · ${recommended.name}",
                        color      = JarvisPalette.TextPrimary,
                        fontSize   = 13.sp,
                        fontWeight = FontWeight.Medium,
                    )
                    Text(
                        text     = "${recommended.sizeFormatted} · ${recommended.ramLabel}",
                        color    = JarvisPalette.TextSecondary,
                        fontSize = 11.sp,
                    )
                }
                Spacer(Modifier.width(8.dp))
                FilledTonalButton(
                    onClick = { onDownload(recommended.id) },
                    colors  = ButtonDefaults.filledTonalButtonColors(
                        containerColor = accent,
                        contentColor   = JarvisPalette.TextOnGold,
                    ),
                    contentPadding = androidx.compose.foundation.layout.PaddingValues(
                        horizontal = 14.dp, vertical = 6.dp,
                    ),
                ) {
                    Icon(
                        imageVector        = Icons.Default.Download,
                        contentDescription = null,
                        modifier           = Modifier.size(16.dp),
                    )
                    Spacer(Modifier.width(6.dp))
                    Text("Download", fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                }
            }
        }
    }
}

/**
 * Picks the smallest Gemma-family model for the first-run recommendation.
 * Falls back to the smallest available model regardless of family if no
 * Gemma model is in the catalog — the hero adapts to what's on offer.
 */
private fun pickRecommendedModel(models: List<ModelEntry>): ModelEntry? {
    if (models.isEmpty()) return null
    val gemmas = models.filter { it.family.lowercase().contains("gemma") }
    val pool   = if (gemmas.isNotEmpty()) gemmas else models
    return pool.minByOrNull { it.sizeBytes }
}

/**
 * Shown when the active routing-mode filter hides every card — tells the user
 * that there's nothing to see in this view rather than leaving a blank canvas.
 */
@Composable
private fun EmptyFilterCard(
    mode:     RoutingMode,
    modifier: Modifier = Modifier,
) {
    val (title, sub) = when (mode) {
        RoutingMode.LOCAL -> "No on-device models yet" to
            "Switch to Cloud to see models you can download, then tap one to pull it to this device."
        RoutingMode.CLOUD -> "Everything is already on this device" to
            "Switch to Local to manage your downloaded models."
        else -> "No models in the catalog" to
            "Pull-to-refresh to try again."
    }
    Column(
        modifier = modifier
            .background(
                color = Color(0xFF141414),
                shape = RoundedCornerShape(14.dp),
            )
            .border(1.dp, Color(0xFF262626), RoundedCornerShape(14.dp))
            .padding(20.dp),
    ) {
        Text(
            text     = title,
            color    = JarvisPalette.TextPrimary,
            fontSize = 14.sp,
            fontWeight = FontWeight.SemiBold,
        )
        Spacer(Modifier.height(4.dp))
        Text(
            text     = sub,
            color    = JarvisPalette.TextSecondary,
            fontSize = 12.sp,
            lineHeight = 16.sp,
        )
    }
}
