package com.jarvis.android.presentation.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.jarvis.android.core.designsystem.JarvisComponentShapes
import com.jarvis.android.core.designsystem.JarvisTheme
import com.jarvis.android.domain.model.SensorReading

/**
 * Displays a single live sensor reading as a compact card.
 *
 * For single-value sensors (light, pressure, temperature) shows a large
 * numeric value. For multi-axis sensors (accelerometer, gyroscope) shows
 * labelled X / Y / Z values.
 */
@Composable
fun SensorCard(
    reading:  SensorReading,
    unit:     String = "",
    modifier: Modifier = Modifier,
) {
    Card(
        modifier = modifier,
        colors   = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant,
        ),
        shape = JarvisComponentShapes.SensorCard,
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text(
                text     = reading.sensorName,
                style    = MaterialTheme.typography.labelSmall,
                color    = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Spacer(Modifier.height(4.dp))

            if (reading.values.size == 1) {
                // Single-value sensor
                Row(
                    modifier              = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment     = Alignment.CenterVertically,
                ) {
                    Text(
                        text  = "%.2f".format(reading.values[0]),
                        style = JarvisTheme.typography.sensorValue,
                        color = MaterialTheme.colorScheme.primary,
                    )
                    if (unit.isNotBlank()) {
                        Text(
                            text  = unit,
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            } else {
                // Multi-axis sensor (up to 3 axes)
                val labels = listOf("X", "Y", "Z", "W")
                reading.values.take(4).forEachIndexed { i, v ->
                    Row(
                        modifier              = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Text(
                            text  = labels.getOrElse(i) { i.toString() },
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Text(
                            text  = "%.3f".format(v),
                            style = JarvisTheme.typography.sensorValue,
                        )
                    }
                }
            }

            Spacer(Modifier.height(4.dp))
            Text(
                text  = "acc=${reading.accuracy}",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f),
            )
        }
    }
}
