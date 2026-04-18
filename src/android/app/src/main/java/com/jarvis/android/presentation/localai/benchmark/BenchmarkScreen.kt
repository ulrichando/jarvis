package com.jarvis.android.presentation.localai.benchmark

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
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.Scaffold
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
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import com.jarvis.android.core.designsystem.JarvisPalette
import com.jarvis.android.domain.model.BenchmarkResult
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

@Composable
fun BenchmarkScreen(viewModel: BenchmarkViewModel = hiltViewModel()) {
    val state    by viewModel.uiState.collectAsState()
    val snackbar  = remember { SnackbarHostState() }

    LaunchedEffect(state.toast) {
        state.toast?.let { snackbar.showSnackbar(it); viewModel.onToastShown() }
    }

    Scaffold(
        containerColor = JarvisPalette.ObsidianBlack,
        snackbarHost   = { SnackbarHost(snackbar) },
    ) { padding ->
        LazyColumn(
            modifier            = Modifier
                .padding(padding)
                .fillMaxSize(),
            contentPadding      = androidx.compose.foundation.layout.PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {

            // ── Run section ───────────────────────────────────────────────────
            item {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(12.dp))
                        .background(JarvisPalette.SurfaceElevated)
                        .border(1.dp, JarvisPalette.GoldBorder, RoundedCornerShape(12.dp))
                        .padding(16.dp)
                ) {
                    Text(
                        text       = "Benchmark",
                        color      = JarvisPalette.GoldGlow,
                        fontSize   = 15.sp,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Spacer(Modifier.height(4.dp))
                    Text(
                        text     = "Runs a 200-token greedy generation and measures TTFT, tokens/sec, peak RAM and CPU.",
                        color    = JarvisPalette.TextSecondary,
                        fontSize = 12.sp,
                        lineHeight = 17.sp,
                    )
                    Spacer(Modifier.height(14.dp))

                    if (state.loadedModelId == null) {
                        Text(
                            "Load a model in the Inference tab to run a benchmark.",
                            color    = JarvisPalette.TextSecondary,
                            fontSize = 12.sp,
                        )
                    } else {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(
                                text     = state.loadedModel?.name ?: state.loadedModelId!!,
                                color    = JarvisPalette.TextPrimary,
                                fontSize = 13.sp,
                                fontWeight = FontWeight.Medium,
                                modifier = Modifier.weight(1f),
                            )
                            FilledTonalButton(
                                onClick  = viewModel::onRunBenchmark,
                                enabled  = state.canRun,
                                colors   = ButtonDefaults.filledTonalButtonColors(
                                    containerColor = JarvisPalette.GoldPrimary,
                                    contentColor   = JarvisPalette.TextOnGold,
                                ),
                                shape    = RoundedCornerShape(8.dp),
                            ) {
                                if (state.isRunning) {
                                    CircularProgressIndicator(
                                        modifier    = Modifier.size(14.dp),
                                        strokeWidth = 2.dp,
                                        color       = JarvisPalette.TextOnGold,
                                    )
                                    Spacer(Modifier.width(6.dp))
                                    Text("Running…", fontSize = 12.sp)
                                } else {
                                    Icon(Icons.Default.PlayArrow, null, modifier = Modifier.size(16.dp))
                                    Spacer(Modifier.width(4.dp))
                                    Text("Run", fontSize = 12.sp)
                                }
                            }
                        }
                    }
                }
            }

            // ── Latest result ─────────────────────────────────────────────────
            state.lastResult?.let { result ->
                item {
                    BenchmarkResultCard(result, isLatest = true)
                }
            }

            // ── History ───────────────────────────────────────────────────────
            if (state.history.isNotEmpty()) {
                item {
                    Text(
                        text       = "History",
                        color      = JarvisPalette.TextSecondary,
                        fontSize   = 12.sp,
                        fontWeight = FontWeight.SemiBold,
                        letterSpacing = 0.8.sp,
                        modifier   = Modifier.padding(vertical = 4.dp),
                    )
                }
                items(state.history, key = { it.timestampMs }) { result ->
                    BenchmarkResultCard(result, isLatest = false)
                }
            }
        }
    }
}

// ── Result card ───────────────────────────────────────────────────────────────

@Composable
private fun BenchmarkResultCard(result: BenchmarkResult, isLatest: Boolean) {
    val borderColor = if (isLatest) JarvisPalette.GoldPrimary else JarvisPalette.GoldBorder

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(JarvisPalette.SurfaceElevated)
            .border(1.dp, borderColor, RoundedCornerShape(12.dp))
            .padding(14.dp)
    ) {
        Row(
            modifier              = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment     = Alignment.CenterVertically,
        ) {
            Text(
                text       = result.modelName,
                color      = if (isLatest) JarvisPalette.GoldGlow else JarvisPalette.TextPrimary,
                fontSize   = 13.sp,
                fontWeight = FontWeight.SemiBold,
                modifier   = Modifier.weight(1f),
            )
            Text(
                text     = SimpleDateFormat("HH:mm  dd MMM", Locale.getDefault()).format(Date(result.timestampMs)),
                color    = JarvisPalette.TextSecondary,
                fontSize = 10.sp,
            )
        }

        Spacer(Modifier.height(12.dp))

        Row(
            modifier              = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceEvenly,
        ) {
            MetricCell("TPS",  "%.1f".format(result.tokensPerSec), JarvisPalette.GoldPrimary)
            MetricCell("TTFT", "${result.ttftMs} ms",              JarvisPalette.SuccessGreen)
            MetricCell("RAM",  "${result.peakRamMb} MB",           JarvisPalette.WarningAmber)
            MetricCell("CPU",  "${result.peakCpuPct}%",            JarvisPalette.TextPrimary)
            MetricCell("GPU",  "${result.gpuLayers}L",             JarvisPalette.GoldMuted)
        }

        if (isLatest) {
            Spacer(Modifier.height(8.dp))
            Text(
                text      = "${result.totalTokens} tokens generated",
                color     = JarvisPalette.TextSecondary,
                fontSize  = 11.sp,
                textAlign = TextAlign.Center,
                modifier  = Modifier.fillMaxWidth(),
            )
        }
    }
}

@Composable
private fun MetricCell(
    label: String,
    value: String,
    color: androidx.compose.ui.graphics.Color,
) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(value, color = color,                        fontSize = 16.sp, fontWeight = FontWeight.Bold)
        Text(label, color = JarvisPalette.TextSecondary,  fontSize = 10.sp, letterSpacing = 0.6.sp)
    }
}
