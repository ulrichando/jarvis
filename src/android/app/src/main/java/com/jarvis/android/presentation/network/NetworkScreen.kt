package com.jarvis.android.presentation.network

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Wifi
import androidx.compose.material.icons.filled.WifiOff
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun NetworkScreen(
    onBack:    () -> Unit = {},
    viewModel: NetworkViewModel = hiltViewModel(),
) {
    val state   by viewModel.uiState.collectAsState()
    val snackbar = remember { SnackbarHostState() }

    LaunchedEffect(state.error) {
        state.error?.let { snackbar.showSnackbar(it); viewModel.onIntent(NetworkIntent.ClearError) }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            TopAppBar(
                title = { Text("Network") },
                navigationIcon = {
                    IconButton(onClick = onBack) { Icon(Icons.Default.ArrowBack, "Back") }
                },
                actions = {
                    IconButton(onClick = { viewModel.onIntent(NetworkIntent.Refresh) }) {
                        Icon(Icons.Default.Refresh, "Refresh")
                    }
                },
            )
        },
    ) { padding ->
        LazyColumn(
            modifier = Modifier
                .padding(padding)
                .fillMaxSize()
                .padding(16.dp),
        ) {
            // Connectivity card
            item {
                ConnectivityCard(state)
                Spacer(Modifier.height(12.dp))
            }

            // Scan button + results
            item {
                Row(
                    modifier  = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text("WiFi Networks", style = MaterialTheme.typography.titleSmall)
                    if (state.isScanning) {
                        CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
                    } else {
                        Button(onClick = { viewModel.onIntent(NetworkIntent.Scan) }) {
                            Text("Scan", style = MaterialTheme.typography.labelSmall)
                        }
                    }
                }
                Spacer(Modifier.height(8.dp))
            }

            if (state.networks.isEmpty() && !state.isScanning) {
                item {
                    Text(
                        "No scan results — tap Scan",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            items(state.networks, key = { it.bssid }) { net ->
                WifiNetworkRow(net)
                HorizontalDivider(thickness = 0.5.dp, color = MaterialTheme.colorScheme.outlineVariant)
            }

            // Route output
            if (state.routeOutput.isNotBlank()) {
                item {
                    Spacer(Modifier.height(16.dp))
                    Text("ip route", style = MaterialTheme.typography.titleSmall)
                    Spacer(Modifier.height(4.dp))
                    Card(Modifier.fillMaxWidth()) {
                        Text(
                            text  = state.routeOutput,
                            style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
                            modifier = Modifier.padding(12.dp),
                        )
                    }
                }
            }

            item {
                Spacer(Modifier.height(12.dp))
                Button(
                    onClick  = { viewModel.onIntent(NetworkIntent.RunRoute) },
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text("Show ip route")
                }
            }
        }
    }
}

@Composable
private fun ConnectivityCard(state: NetworkUiState) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(
                    imageVector = if (state.isConnected) Icons.Default.Wifi else Icons.Default.WifiOff,
                    contentDescription = null,
                    tint = if (state.isConnected) MaterialTheme.colorScheme.primary
                           else MaterialTheme.colorScheme.error,
                    modifier = Modifier.size(20.dp).padding(end = 8.dp),
                )
                Text(
                    text  = if (state.isConnected) "Connected (${state.transport})" else "Disconnected",
                    style = MaterialTheme.typography.titleSmall,
                    color = if (state.isConnected) MaterialTheme.colorScheme.primary
                            else MaterialTheme.colorScheme.error,
                )
            }
            if (state.ssid.isNotBlank()) {
                Spacer(Modifier.height(8.dp))
                NetInfoRow("SSID",  state.ssid)
            }
            if (state.ipv4.isNotBlank()) {
                NetInfoRow("IPv4", state.ipv4)
            }
            if (state.ipv6.isNotBlank()) {
                NetInfoRow("IPv6", state.ipv6)
            }
        }
    }
}

@Composable
private fun WifiNetworkRow(net: WifiNetwork) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 4.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            Icons.Default.Wifi, null,
            tint     = rssiTint(net.rssi),
            modifier = Modifier.size(18.dp).padding(end = 8.dp),
        )
        Column(Modifier.weight(1f)) {
            Text(net.ssid.ifBlank { "<hidden>" }, style = MaterialTheme.typography.bodyMedium)
            Text(
                "${net.bssid}  ch${net.channel}  ${net.frequency} MHz  ${net.security}",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Text(
            "${net.rssi} dBm",
            style = MaterialTheme.typography.labelSmall,
            color = rssiTint(net.rssi),
        )
    }
}

@Composable
private fun NetInfoRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
        Text(
            label,
            style    = MaterialTheme.typography.bodySmall,
            color    = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.weight(1f),
        )
        Text(value, style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun rssiTint(rssi: Int) = when {
    rssi >= -60 -> MaterialTheme.colorScheme.primary
    rssi >= -75 -> MaterialTheme.colorScheme.tertiary
    else         -> MaterialTheme.colorScheme.error
}
