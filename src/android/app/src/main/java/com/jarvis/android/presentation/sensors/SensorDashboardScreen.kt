package com.jarvis.android.presentation.sensors

import android.hardware.Sensor
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.PrimaryTabRow
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Tab
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.jarvis.android.domain.model.LocationReading
import com.jarvis.android.domain.model.OrientationReading
import com.jarvis.android.domain.model.SensorReading
import com.jarvis.android.presentation.components.SensorCard

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SensorDashboardScreen(
    onBack:    () -> Unit = {},
    viewModel: SensorViewModel = hiltViewModel(),
) {
    val state   by viewModel.uiState.collectAsState()
    val snackbar = remember { SnackbarHostState() }

    LaunchedEffect(state.error) {
        state.error?.let { snackbar.showSnackbar(it); viewModel.onIntent(SensorIntent.ClearError) }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            androidx.compose.material3.TopAppBar(
                title = { Text("Sensors") },
                navigationIcon = {
                    IconButton(onClick = onBack) { Icon(Icons.Default.ArrowBack, "Back") }
                },
            )
        },
    ) { padding ->
        Column(Modifier.padding(padding).fillMaxSize()) {
            val tabs = SensorTab.values()
            PrimaryTabRow(selectedTabIndex = tabs.indexOf(state.activeTab)) {
                tabs.forEach { tab ->
                    Tab(
                        selected = tab == state.activeTab,
                        onClick  = { viewModel.onIntent(SensorIntent.SelectTab(tab)) },
                        text     = { Text(tab.name.lowercase().replaceFirstChar { it.uppercase() },
                            style = MaterialTheme.typography.labelSmall) },
                    )
                }
            }

            when (state.activeTab) {
                SensorTab.SENSORS     -> SensorsGrid(state)
                SensorTab.LOCATION    -> LocationTab(state.location)
                SensorTab.ORIENTATION -> OrientationTab(state.orientation)
            }
        }
    }
}

@Composable
private fun SensorsGrid(state: SensorUiState) {
    if (state.readings.isEmpty()) {
        Column(
            Modifier.fillMaxSize().padding(32.dp),
            verticalArrangement = Arrangement.Center,
        ) {
            Text(
                "Waiting for sensor data…",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        return
    }

    LazyVerticalGrid(
        columns  = GridCells.Fixed(2),
        modifier = Modifier.fillMaxSize().padding(8.dp),
    ) {
        items(state.readings.values.toList(), key = { it.sensorType }) { reading ->
            SensorCard(
                reading  = reading,
                unit     = unitFor(reading.sensorType),
                modifier = Modifier.padding(4.dp),
            )
        }
    }
}

@Composable
private fun LocationTab(location: LocationReading?) {
    if (location == null) {
        Text(
            "No location fix yet — ensure location permission is granted.",
            style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier.padding(24.dp),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        return
    }
    LazyColumn(Modifier.fillMaxSize().padding(16.dp)) {
        item {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(12.dp)) {
                    Text("GPS / Location", style = MaterialTheme.typography.titleSmall)
                    Spacer(Modifier.height(8.dp))
                    LocRow("Latitude",  "%.6f°".format(location.latitudeDeg))
                    LocRow("Longitude", "%.6f°".format(location.longitudeDeg))
                    LocRow("Altitude",  "%.1f m".format(location.altitudeM))
                    LocRow("Accuracy",  "%.1f m".format(location.accuracyM))
                    LocRow("Speed",     "%.2f m/s".format(location.speedMps))
                    LocRow("Bearing",   "%.1f°".format(location.bearingDeg))
                    LocRow("Provider",  location.provider)
                }
            }
        }
    }
}

@Composable
private fun OrientationTab(orientation: OrientationReading?) {
    if (orientation == null) {
        Text(
            "No orientation data — accelerometer + magnetometer required.",
            style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier.padding(24.dp),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        return
    }
    LazyColumn(Modifier.fillMaxSize().padding(16.dp)) {
        item {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(12.dp)) {
                    Text("Device Orientation", style = MaterialTheme.typography.titleSmall)
                    Spacer(Modifier.height(8.dp))
                    LocRow("Azimuth", "%.1f°  (${compassPoint(orientation.azimuthDeg)})".format(orientation.azimuthDeg))
                    LocRow("Pitch",   "%.1f°".format(orientation.pitchDeg))
                    LocRow("Roll",    "%.1f°".format(orientation.rollDeg))
                }
            }
        }

        item {
            Spacer(Modifier.height(12.dp))
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(12.dp)) {
                    Text("Device Orientation", style = MaterialTheme.typography.titleSmall)
                    Spacer(Modifier.height(4.dp))
                    Text(
                        "Derived from accelerometer + magnetometer via SensorManager.getRotationMatrix",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

@Composable
private fun LocRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
        Text(
            label,
            style    = MaterialTheme.typography.bodySmall,
            color    = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.weight(1f),
        )
        Text(value, style = MaterialTheme.typography.bodySmall)
    }
    HorizontalDivider(thickness = 0.5.dp, color = MaterialTheme.colorScheme.outlineVariant)
}

private fun unitFor(type: Int): String = when (type) {
    Sensor.TYPE_ACCELEROMETER,
    Sensor.TYPE_GRAVITY,
    Sensor.TYPE_LINEAR_ACCELERATION -> "m/s²"
    Sensor.TYPE_GYROSCOPE            -> "rad/s"
    Sensor.TYPE_MAGNETIC_FIELD       -> "μT"
    Sensor.TYPE_LIGHT                -> "lx"
    Sensor.TYPE_PRESSURE             -> "hPa"
    Sensor.TYPE_AMBIENT_TEMPERATURE  -> "°C"
    Sensor.TYPE_RELATIVE_HUMIDITY    -> "%"
    Sensor.TYPE_PROXIMITY            -> "cm"
    Sensor.TYPE_STEP_COUNTER         -> "steps"
    else                             -> ""
}

private fun compassPoint(azimuth: Float): String {
    val directions = arrayOf("N","NE","E","SE","S","SW","W","NW","N")
    return directions[((azimuth + 22.5f) / 45f).toInt().coerceIn(0, 8)]
}
