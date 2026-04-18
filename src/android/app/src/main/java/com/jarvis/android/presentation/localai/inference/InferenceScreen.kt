package com.jarvis.android.presentation.localai.inference

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Clear
import androidx.compose.material.icons.filled.Memory
import androidx.compose.material.icons.filled.Send
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MenuAnchorType
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import com.jarvis.android.core.designsystem.JarvisPalette

@Composable
fun InferenceScreen(viewModel: InferenceViewModel = hiltViewModel()) {
    val state    by viewModel.uiState.collectAsState()
    val snackbar  = remember { SnackbarHostState() }
    val scrollState = rememberScrollState()

    LaunchedEffect(state.response) { scrollState.animateScrollTo(scrollState.maxValue) }
    LaunchedEffect(state.toast) {
        state.toast?.let { snackbar.showSnackbar(it); viewModel.onToastShown() }
    }

    Scaffold(
        containerColor = JarvisPalette.ObsidianBlack,
        snackbarHost   = { SnackbarHost(snackbar) },
    ) { padding ->
        Column(
            modifier = Modifier
                .padding(padding)
                .fillMaxSize()
                .imePadding(),
        ) {

            // ── Model selector + Load button ──────────────────────────────────
            Row(
                modifier              = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 12.dp),
                verticalAlignment     = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                ModelDropdown(
                    models    = state.downloadedModels.map { it.id to it.name },
                    selected  = state.selectedModelId,
                    onSelect  = viewModel::onSelectModel,
                    modifier  = Modifier.weight(1f),
                )
                if (state.canLoad) {
                    FilledTonalButton(
                        onClick  = viewModel::onLoad,
                        colors   = ButtonDefaults.filledTonalButtonColors(
                            containerColor = JarvisPalette.GoldPrimary,
                            contentColor   = JarvisPalette.TextOnGold,
                        ),
                        shape    = RoundedCornerShape(8.dp),
                    ) {
                        Icon(Icons.Default.Memory, null, modifier = Modifier.size(14.dp))
                        Spacer(Modifier.width(4.dp))
                        Text("Load", fontSize = 12.sp)
                    }
                }
            }

            // ── Load progress ─────────────────────────────────────────────────
            AnimatedVisibility(state.isLoading) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 4.dp)
                        .clip(RoundedCornerShape(8.dp))
                        .background(JarvisPalette.GoldDim)
                        .padding(12.dp)
                ) {
                    Text(state.loadStatus, color = JarvisPalette.GoldGlow, fontSize = 12.sp)
                    Spacer(Modifier.height(6.dp))
                    LinearProgressIndicator(
                        modifier   = Modifier.fillMaxWidth(),
                        color      = JarvisPalette.GoldPrimary,
                        trackColor = JarvisPalette.GoldBorder,
                    )
                }
            }

            // ── No model loaded prompt ────────────────────────────────────────
            if (state.loadedModelId == null && !state.isLoading) {
                Box(
                    modifier            = Modifier
                        .fillMaxWidth()
                        .padding(16.dp)
                        .clip(RoundedCornerShape(10.dp))
                        .background(JarvisPalette.SurfaceElevated)
                        .border(1.dp, JarvisPalette.GoldBorder, RoundedCornerShape(10.dp))
                        .padding(16.dp),
                    contentAlignment    = Alignment.Center,
                ) {
                    Text(
                        text     = if (state.downloadedModels.isEmpty())
                            "No models downloaded.\nGo to the Models tab to download one."
                        else
                            "Select a model above and tap Load to begin local inference.",
                        color    = JarvisPalette.TextSecondary,
                        fontSize = 13.sp,
                        lineHeight = 18.sp,
                    )
                }
            }

            // ── Response area ─────────────────────────────────────────────────
            if (state.response.isNotEmpty() || state.isGenerating) {
                Column(
                    modifier = Modifier
                        .weight(1f)
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 4.dp)
                        .clip(RoundedCornerShape(10.dp))
                        .background(JarvisPalette.CodeBg)
                        .border(1.dp, JarvisPalette.CodeBorder, RoundedCornerShape(10.dp))
                        .padding(14.dp)
                        .verticalScroll(scrollState)
                ) {
                    // Loaded model tag
                    state.loadedModel?.let { model ->
                        Text(
                            text       = model.name,
                            color      = JarvisPalette.GoldMuted,
                            fontSize   = 10.sp,
                            fontWeight = FontWeight.SemiBold,
                            letterSpacing = 0.8.sp,
                        )
                        Spacer(Modifier.height(8.dp))
                    }

                    Text(
                        text       = state.response,
                        color      = JarvisPalette.TextPrimary,
                        fontSize   = 13.sp,
                        lineHeight  = 20.sp,
                        fontFamily = FontFamily.Default,
                    )

                    if (state.isGenerating) {
                        Spacer(Modifier.height(4.dp))
                        Box(
                            Modifier
                                .size(8.dp, 16.dp)
                                .background(JarvisPalette.GoldPrimary, RoundedCornerShape(2.dp))
                        )
                    }
                }
            } else {
                Spacer(Modifier.weight(1f))
            }

            // ── Input bar ─────────────────────────────────────────────────────
            Row(
                modifier              = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 12.dp, vertical = 8.dp),
                verticalAlignment     = Alignment.Bottom,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                OutlinedTextField(
                    value         = state.inputText,
                    onValueChange = viewModel::onInputChange,
                    placeholder   = { Text("Prompt…", color = JarvisPalette.TextDisabled, fontSize = 13.sp) },
                    minLines      = 1,
                    maxLines      = 5,
                    enabled       = !state.isGenerating,
                    shape         = RoundedCornerShape(12.dp),
                    colors        = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor   = JarvisPalette.GoldPrimary,
                        unfocusedBorderColor = JarvisPalette.GoldBorder,
                        focusedTextColor     = JarvisPalette.TextPrimary,
                        unfocusedTextColor   = JarvisPalette.TextPrimary,
                        cursorColor          = JarvisPalette.GoldPrimary,
                        focusedContainerColor   = JarvisPalette.SurfaceElevated,
                        unfocusedContainerColor = JarvisPalette.SurfaceDark,
                    ),
                    modifier      = Modifier.weight(1f),
                )

                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    if (state.isGenerating) {
                        IconButton(
                            onClick  = viewModel::onStop,
                            modifier = Modifier
                                .size(40.dp)
                                .clip(RoundedCornerShape(10.dp))
                                .background(JarvisPalette.ErrorContainer),
                        ) {
                            Icon(Icons.Default.Stop, "Stop", tint = JarvisPalette.ErrorRed, modifier = Modifier.size(20.dp))
                        }
                    } else {
                        IconButton(
                            onClick  = viewModel::onGenerate,
                            enabled  = state.canGenerate,
                            modifier = Modifier
                                .size(40.dp)
                                .clip(RoundedCornerShape(10.dp))
                                .background(
                                    if (state.canGenerate) JarvisPalette.GoldPrimary
                                    else JarvisPalette.SurfaceElevated
                                ),
                        ) {
                            Icon(
                                Icons.Default.Send, "Send",
                                tint     = if (state.canGenerate) JarvisPalette.TextOnGold else JarvisPalette.TextDisabled,
                                modifier = Modifier.size(18.dp),
                            )
                        }
                    }

                    if (state.response.isNotEmpty() || state.inputText.isNotEmpty()) {
                        IconButton(
                            onClick  = viewModel::onClearResponse,
                            modifier = Modifier
                                .size(40.dp)
                                .clip(RoundedCornerShape(10.dp))
                                .background(JarvisPalette.SurfaceElevated),
                        ) {
                            Icon(Icons.Default.Clear, "Clear", tint = JarvisPalette.TextSecondary, modifier = Modifier.size(16.dp))
                        }
                    }
                }
            }
        }
    }
}

// ── Model dropdown ────────────────────────────────────────────────────────────

@Composable
private fun ModelDropdown(
    models:   List<Pair<String, String>>,
    selected: String?,
    onSelect: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    var expanded by remember { mutableStateOf(false) }
    val selectedName = models.find { it.first == selected }?.second ?: "No models downloaded"

    ExposedDropdownMenuBox(expanded = expanded, onExpandedChange = { if (models.isNotEmpty()) expanded = it }, modifier = modifier) {
        OutlinedTextField(
            value         = selectedName,
            onValueChange = {},
            readOnly      = true,
            singleLine    = true,
            trailingIcon  = { if (models.isNotEmpty()) ExposedDropdownMenuDefaults.TrailingIcon(expanded) },
            colors        = OutlinedTextFieldDefaults.colors(
                focusedBorderColor   = JarvisPalette.GoldPrimary,
                unfocusedBorderColor = JarvisPalette.GoldBorder,
                focusedTextColor     = JarvisPalette.TextPrimary,
                unfocusedTextColor   = JarvisPalette.TextPrimary,
                focusedContainerColor   = JarvisPalette.SurfaceElevated,
                unfocusedContainerColor = JarvisPalette.SurfaceDark,
            ),
            shape    = RoundedCornerShape(10.dp),
            modifier = Modifier.fillMaxWidth().menuAnchor(MenuAnchorType.PrimaryEditable),
        )
        ExposedDropdownMenu(
            expanded         = expanded,
            onDismissRequest = { expanded = false },
            containerColor   = JarvisPalette.SurfaceElevated,
        ) {
            models.forEach { (id, name) ->
                DropdownMenuItem(
                    text    = { Text(name, color = JarvisPalette.TextPrimary, fontSize = 13.sp) },
                    onClick = { onSelect(id); expanded = false },
                )
            }
        }
    }
}
