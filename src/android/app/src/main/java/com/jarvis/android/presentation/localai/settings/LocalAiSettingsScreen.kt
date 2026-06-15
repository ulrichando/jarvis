package com.jarvis.android.presentation.localai.settings

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import com.jarvis.android.core.designsystem.JarvisPalette

@Composable
fun LocalAiSettingsScreen(viewModel: LocalAiSettingsViewModel = hiltViewModel()) {
    val state    by viewModel.uiState.collectAsState()
    val snackbar  = remember { SnackbarHostState() }

    LaunchedEffect(state.isSaved) {
        if (state.isSaved) {
            snackbar.showSnackbar("Settings saved")
            viewModel.onDismissSaved()
        }
    }

    Scaffold(
        containerColor = JarvisPalette.ObsidianBlack,
        snackbarHost   = { SnackbarHost(snackbar) },
    ) { padding ->
        Column(
            modifier = Modifier
                .padding(padding)
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {

            // ── llama.cpp section ─────────────────────────────────────────────
            SettingsSection(title = "llama.cpp") {

                // GPU Layers
                SettingRow(
                    label   = "GPU Layers",
                    value   = if (state.gpuLayers == 0) "CPU only" else "${state.gpuLayers} layers",
                    hint    = "Number of transformer layers to offload to GPU (Vulkan). 0 = CPU only.",
                ) {
                    Slider(
                        value         = state.gpuLayers.toFloat(),
                        onValueChange = { viewModel.onGpuLayersChange(it.toInt()) },
                        valueRange    = 0f..50f,
                        steps         = 49,
                        colors        = SliderDefaults.colors(
                            thumbColor       = JarvisPalette.GoldPrimary,
                            activeTrackColor = JarvisPalette.GoldPrimary,
                            inactiveTrackColor = JarvisPalette.GoldBorder,
                        ),
                        modifier      = Modifier.fillMaxWidth(),
                    )
                }

                Spacer(Modifier.height(4.dp))

                // Context size
                SettingRow(
                    label = "Context Window",
                    value = "${state.contextSize} tokens",
                    hint  = "KV cache size in tokens. Larger = longer conversations, more RAM.",
                ) {
                    Row(
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment     = Alignment.CenterVertically,
                    ) {
                        listOf(2048, 4096, 8192, 16384, 32768).forEach { size ->
                            val selected = state.contextSize == size
                            Text(
                                text       = if (size >= 1024) "${size / 1024}K" else "$size",
                                color      = if (selected) JarvisPalette.GoldGlow else JarvisPalette.TextSecondary,
                                fontSize   = 12.sp,
                                fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
                                modifier   = Modifier
                                    .clip(RoundedCornerShape(6.dp))
                                    .background(
                                        if (selected) JarvisPalette.GoldDim else JarvisPalette.SurfaceOverlay
                                    )
                                    .then(
                                        if (selected) Modifier.border(1.dp, JarvisPalette.GoldPrimary, RoundedCornerShape(6.dp))
                                        else Modifier
                                    )
                                    .padding(horizontal = 10.dp, vertical = 5.dp)
                                    .also { /* clickable needs Modifier.clickable */ }
                                    .run {
                                        // wire tap — we reuse the slot rather than adding a nested clickable
                                        this
                                    },
                            )
                        }
                    }
                    // Slider for fine-grained control
                    Slider(
                        value         = when (state.contextSize) {
                            2048  -> 0f; 4096  -> 1f; 8192  -> 2f
                            16384 -> 3f; else  -> 4f
                        },
                        onValueChange = {
                            val sizes = listOf(2048, 4096, 8192, 16384, 32768)
                            viewModel.onContextSizeChange(sizes[it.toInt().coerceIn(0, 4)])
                        },
                        valueRange    = 0f..4f,
                        steps         = 3,
                        colors        = SliderDefaults.colors(
                            thumbColor       = JarvisPalette.GoldPrimary,
                            activeTrackColor = JarvisPalette.GoldPrimary,
                            inactiveTrackColor = JarvisPalette.GoldBorder,
                        ),
                        modifier      = Modifier.fillMaxWidth(),
                    )
                }

                Spacer(Modifier.height(4.dp))

                // Threads
                SettingRow(
                    label = "CPU Threads",
                    value = "${state.nThreads} threads",
                    hint  = "Parallel threads for CPU inference. Match your device's performance cores.",
                ) {
                    Slider(
                        value         = state.nThreads.toFloat(),
                        onValueChange = { viewModel.onThreadsChange(it.toInt()) },
                        valueRange    = 1f..16f,
                        steps         = 14,
                        colors        = SliderDefaults.colors(
                            thumbColor       = JarvisPalette.GoldPrimary,
                            activeTrackColor = JarvisPalette.GoldPrimary,
                            inactiveTrackColor = JarvisPalette.GoldBorder,
                        ),
                        modifier      = Modifier.fillMaxWidth(),
                    )
                }
            }

            // ── Save ──────────────────────────────────────────────────────────
            FilledTonalButton(
                onClick  = viewModel::onSave,
                enabled  = state.isDirty,
                colors   = ButtonDefaults.filledTonalButtonColors(
                    containerColor         = JarvisPalette.GoldPrimary,
                    contentColor           = JarvisPalette.TextOnGold,
                    disabledContainerColor = JarvisPalette.GoldBorder,
                    disabledContentColor   = JarvisPalette.TextDisabled,
                ),
                shape    = RoundedCornerShape(10.dp),
                modifier = Modifier.fillMaxWidth().height(48.dp),
            ) {
                Text("Save Settings", fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
            }

            Spacer(Modifier.height(16.dp))
        }
    }
}

// ── Section container ─────────────────────────────────────────────────────────

@Composable
private fun SettingsSection(
    title:   String,
    content: @Composable () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(JarvisPalette.SurfaceElevated)
            .border(1.dp, JarvisPalette.GoldBorder, RoundedCornerShape(12.dp))
            .padding(16.dp)
    ) {
        Text(
            text       = title.uppercase(),
            color      = JarvisPalette.GoldPrimary,
            fontSize   = 11.sp,
            fontWeight = FontWeight.Bold,
            letterSpacing = 1.sp,
        )
        Spacer(Modifier.height(14.dp))
        content()
    }
}

// ── Setting row ───────────────────────────────────────────────────────────────

@Composable
private fun SettingRow(
    label:   String,
    value:   String,
    hint:    String,
    control: @Composable () -> Unit,
) {
    Column {
        Row(
            modifier              = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment     = Alignment.CenterVertically,
        ) {
            Text(label, color = JarvisPalette.TextPrimary,   fontSize = 13.sp, fontWeight = FontWeight.Medium)
            Text(value, color = JarvisPalette.GoldGlow,       fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
        }
        Text(hint, color = JarvisPalette.TextSecondary, fontSize = 11.sp, lineHeight = 15.sp)
        Spacer(Modifier.height(6.dp))
        control()
    }
}
