package com.jarvis.android.presentation.system

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.PrimaryTabRow
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Tab
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.jarvis.android.domain.model.SystemInfo
import com.jarvis.android.presentation.components.ProcessRow

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SystemDashboardScreen(
    onBack:    () -> Unit = {},
    viewModel: SystemViewModel = hiltViewModel(),
) {
    val state    by viewModel.uiState.collectAsState()
    val snackbar  = remember { SnackbarHostState() }

    LaunchedEffect(state.error) {
        state.error?.let { snackbar.showSnackbar(it); viewModel.onIntent(SystemIntent.ClearError) }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            TopAppBar(
                title = { Text("System") },
                navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.Default.ArrowBack, "Back") } },
                actions = { IconButton(onClick = { viewModel.onIntent(SystemIntent.Refresh) }) {
                    Icon(Icons.Default.Refresh, "Refresh")
                } },
            )
        },
    ) { padding ->
        Column(Modifier.padding(padding).fillMaxSize()) {
            val tabs = SystemTab.values()
            PrimaryTabRow(selectedTabIndex = tabs.indexOf(state.activeTab)) {
                tabs.forEach { tab ->
                    Tab(
                        selected = tab == state.activeTab,
                        onClick  = { viewModel.onIntent(SystemIntent.SelectTab(tab)) },
                        text     = { Text(tab.name.lowercase().replaceFirstChar { it.uppercase() },
                            style = MaterialTheme.typography.labelSmall) },
                    )
                }
            }

            when (state.activeTab) {
                SystemTab.OVERVIEW  -> OverviewTab(state.systemInfo)
                SystemTab.PROCESSES -> ProcessesTab(state, viewModel)
                SystemTab.APPS      -> AppsTab(state)
                SystemTab.LOGCAT    -> LogcatTab(state, viewModel)
            }
        }
    }
}

@Composable
private fun OverviewTab(info: SystemInfo?) {
    if (info == null) { LinearProgressIndicator(Modifier.fillMaxWidth()); return }
    LazyColumn(Modifier.fillMaxSize().padding(16.dp)) {
        item {
            InfoCard("Device") {
                InfoRow("Model", info.deviceModel)
                InfoRow("Android", "${info.androidVersion} (SDK ${info.sdkInt})")
                InfoRow("Arch", info.arch)
                InfoRow("Kernel", info.kernelVersion)
                InfoRow("Root", if (info.isRooted) "Yes" else "No")
            }
            Spacer(Modifier.height(12.dp))
            InfoCard("Memory") {
                InfoRow("Total RAM", "${info.ramTotalMb} MB")
                InfoRow("Available", "${info.ramAvailMb} MB")
                InfoRow("Low memory", if (info.ramLowMemory) "Yes" else "No")
                LinearProgressIndicator(
                    progress = { 1f - info.ramAvailMb.toFloat() / info.ramTotalMb.toFloat() },
                    modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
                )
            }
            Spacer(Modifier.height(12.dp))
            InfoCard("Battery") {
                InfoRow("Level", "${info.batteryPct}%")
                InfoRow("Charging", if (info.batteryCharging) "Yes" else "No")
                LinearProgressIndicator(
                    progress = { info.batteryPct / 100f },
                    modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
                )
            }
            Spacer(Modifier.height(12.dp))
            InfoCard("Uptime") {
                val h = info.uptimeMs / 3_600_000
                val m = (info.uptimeMs % 3_600_000) / 60_000
                InfoRow("Uptime", "${h}h ${m}m")
            }
        }
    }
}

@Composable
private fun ProcessesTab(state: SystemUiState, vm: SystemViewModel) {
    LazyColumn(Modifier.fillMaxSize()) {
        item {
            Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp)) {
                Text("PID", style = MaterialTheme.typography.labelSmall,
                    modifier = Modifier.weight(0.15f))
                Text("Name", style = MaterialTheme.typography.labelSmall,
                    modifier = Modifier.weight(0.45f))
                Text("RSS", style = MaterialTheme.typography.labelSmall,
                    modifier = Modifier.weight(0.2f))
                Text("CPU%", style = MaterialTheme.typography.labelSmall,
                    modifier = Modifier.weight(0.2f))
            }
            HorizontalDivider()
        }
        items(state.processes, key = { it.pid }) { proc ->
            ProcessRow(
                process = proc,
                onKill  = { vm.onIntent(SystemIntent.KillProcess(it.pid)) },
            )
        }
    }
}

@Composable
private fun AppsTab(state: SystemUiState) {
    LazyColumn(Modifier.fillMaxSize()) {
        items(state.apps, key = { it.packageName }) { app ->
            Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp)) {
                Text(app.label, style = MaterialTheme.typography.bodyMedium)
                Text(app.packageName, style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            HorizontalDivider(thickness = 0.5.dp)
        }
    }
}

@Composable
private fun LogcatTab(state: SystemUiState, vm: SystemViewModel) {
    Column(Modifier.fillMaxSize()) {
        OutlinedTextField(
            value = state.logcatTag, onValueChange = { vm.onIntent(SystemIntent.SetLogcatTag(it)) },
            label = { Text("Tag filter") }, singleLine = true,
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
        )
        LazyColumn(Modifier.fillMaxSize().padding(horizontal = 8.dp)) {
            items(state.logcat) { line ->
                Text(line, style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
                    color = logcatColor(line))
            }
        }
    }
}

private fun logcatColor(line: String) = when {
    line.contains(" E ") || line.contains("/E:") -> androidx.compose.ui.graphics.Color(0xFFFF5555)
    line.contains(" W ") || line.contains("/W:") -> androidx.compose.ui.graphics.Color(0xFFFFFF55)
    else -> androidx.compose.ui.graphics.Color.Unspecified
}

@Composable
private fun InfoCard(title: String, content: @Composable () -> Unit) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp)) {
            Text(title, style = MaterialTheme.typography.titleSmall)
            Spacer(Modifier.height(8.dp))
            content()
        }
    }
}

@Composable
private fun InfoRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
        Text(label, style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(1f))
        Text(value, style = MaterialTheme.typography.bodySmall)
    }
}
