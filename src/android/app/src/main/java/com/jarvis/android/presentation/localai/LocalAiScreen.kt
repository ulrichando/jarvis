package com.jarvis.android.presentation.localai

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.BarChart
import androidx.compose.material.icons.filled.Memory
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.SmartToy
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.TabRowDefaults
import androidx.compose.material3.TabRowDefaults.tabIndicatorOffset
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.jarvis.android.core.designsystem.JarvisPalette
import com.jarvis.android.presentation.localai.benchmark.BenchmarkScreen
import com.jarvis.android.presentation.localai.inference.InferenceScreen
import com.jarvis.android.presentation.localai.models.ModelsScreen
import com.jarvis.android.presentation.localai.settings.LocalAiSettingsScreen

// Canonical accent blue — kept locally so theme swaps live in one place per
// screen and match ChatScreen / HomeHero / VoiceOverlay.
private val JarvisAccent = Color(0xFF1E7FFF)

/**
 * Root composable for the Local AI section.
 *
 * Four tabs:
 *   - **Models**    — catalog, download management, routing mode selector
 *   - **Inference** — load a model and run interactive local chat
 *   - **Benchmark** — run and compare inference performance
 *   - **Settings**  — GPU layers, context size, threads
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LocalAiScreen(
    onBack: () -> Unit = {},
) {
    var selectedTab by rememberSaveable { mutableIntStateOf(0) }

    Scaffold(
        containerColor = JarvisPalette.ObsidianBlack,
        topBar = {
            Column {
                TopAppBar(
                    title = {
                        Text(
                            text       = "Local AI",
                            color      = JarvisAccent,
                            fontWeight = FontWeight.SemiBold,
                            fontSize   = 18.sp,
                        )
                    },
                    navigationIcon = {
                        IconButton(onClick = onBack) {
                            Icon(Icons.Default.ArrowBack, "Back", tint = JarvisAccent)
                        }
                    },
                    colors = TopAppBarDefaults.topAppBarColors(
                        containerColor = JarvisPalette.SurfaceDark,
                    ),
                )

                TabRow(
                    selectedTabIndex  = selectedTab,
                    containerColor    = JarvisPalette.SurfaceDark,
                    contentColor      = JarvisAccent,
                    indicator         = { tabPositions ->
                        if (selectedTab < tabPositions.size) {
                            TabRowDefaults.SecondaryIndicator(
                                modifier = Modifier.tabIndicatorOffset(tabPositions[selectedTab]),
                                color    = JarvisAccent,
                                height   = 2.dp,
                            )
                        }
                    },
                    divider = {},
                ) {
                    TABS.forEachIndexed { index, tab ->
                        Tab(
                            selected = selectedTab == index,
                            onClick  = { selectedTab = index },
                            icon     = {
                                Icon(
                                    imageVector = tab.icon,
                                    contentDescription = null,
                                    tint = if (selectedTab == index)
                                        JarvisAccent else JarvisPalette.TextSecondary,
                                )
                            },
                            text     = {
                                Text(
                                    text      = tab.label,
                                    fontSize  = 11.sp,
                                    color     = if (selectedTab == index)
                                        JarvisAccent else JarvisPalette.TextSecondary,
                                )
                            },
                        )
                    }
                }
            }
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .padding(padding)
                .fillMaxSize()
        ) {
            when (selectedTab) {
                0    -> ModelsScreen()
                1    -> InferenceScreen()
                2    -> BenchmarkScreen()
                else -> LocalAiSettingsScreen()
            }
        }
    }
}

// ── Tab descriptors ───────────────────────────────────────────────────────────

private data class TabInfo(val label: String, val icon: ImageVector)

private val TABS = listOf(
    TabInfo("Models",    Icons.Default.Memory),
    TabInfo("Inference", Icons.Default.SmartToy),
    TabInfo("Benchmark", Icons.Default.BarChart),
    TabInfo("Settings",  Icons.Default.Settings),
)
