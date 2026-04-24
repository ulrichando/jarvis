package com.jarvis.android.presentation.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.jarvis.android.core.designsystem.JarvisPalette
import com.jarvis.android.domain.model.ModelConfig

/**
 * Per-model configuration dialog — mirrors Google AI Edge Gallery's
 * "Configurations" sheet (the screenshot the user pointed at):
 *
 *   Max tokens  | slider + numeric field
 *   TopK        | slider + numeric field
 *   TopP        | slider + numeric field
 *   Temperature | slider + numeric field
 *   Accelerator | GPU / CPU filter chips
 *   Enable thinking | toggle
 *
 * Values persist to [com.jarvis.android.data.repository.ApiKeyProviderImpl]
 * keyed by model id, so each downloaded model remembers its own tuning
 * across launches.
 */
@Composable
fun ModelConfigDialog(
    modelName: String,
    initial:   ModelConfig,
    onSave:    (ModelConfig) -> Unit,
    onDismiss: () -> Unit,
) {
    var maxTokens      by remember { mutableStateOf(initial.maxTokens) }
    var topK           by remember { mutableStateOf(initial.topK) }
    var topP           by remember { mutableStateOf(initial.topP) }
    var temperature    by remember { mutableStateOf(initial.temperature) }
    var accelerator    by remember { mutableStateOf(initial.accelerator) }
    var enableThinking by remember { mutableStateOf(initial.enableThinking) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = {
            Column {
                Text("Configurations", style = MaterialTheme.typography.titleLarge)
                Text(
                    modelName,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        },
        text = {
            Column {
                SliderRow(
                    label    = "Max tokens",
                    value    = maxTokens.toFloat(),
                    range    = 500f..32_000f,
                    onChange = { maxTokens = it.toInt() },
                    format   = { it.toInt().toString() },
                )
                Spacer(Modifier.height(12.dp))
                SliderRow(
                    label    = "TopK",
                    value    = topK.toFloat(),
                    range    = 1f..128f,
                    onChange = { topK = it.toInt() },
                    format   = { it.toInt().toString() },
                )
                Spacer(Modifier.height(12.dp))
                SliderRow(
                    label    = "TopP",
                    value    = topP,
                    range    = 0f..1f,
                    onChange = { topP = it },
                    format   = { "%.2f".format(it) },
                )
                Spacer(Modifier.height(12.dp))
                SliderRow(
                    label    = "Temperature",
                    value    = temperature,
                    range    = 0f..2f,
                    onChange = { temperature = it },
                    format   = { "%.2f".format(it) },
                )

                Spacer(Modifier.height(20.dp))
                Text(
                    "Accelerator",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(6.dp))
                Row {
                    FilterChip(
                        selected = accelerator == ModelConfig.Accelerator.GPU,
                        onClick  = { accelerator = ModelConfig.Accelerator.GPU },
                        label    = { Text("GPU") },
                    )
                    Spacer(Modifier.width(8.dp))
                    FilterChip(
                        selected = accelerator == ModelConfig.Accelerator.CPU,
                        onClick  = { accelerator = ModelConfig.Accelerator.CPU },
                        label    = { Text("CPU") },
                    )
                }

                Spacer(Modifier.height(20.dp))
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier          = Modifier.fillMaxWidth(),
                ) {
                    Column(Modifier.weight(1f)) {
                        Text(
                            "Enable thinking",
                            style = MaterialTheme.typography.bodyMedium,
                        )
                        Text(
                            "Show reasoning traces from Gemma 4 / DeepSeek R1",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    Switch(
                        checked         = enableThinking,
                        onCheckedChange = { enableThinking = it },
                    )
                }
            }
        },
        confirmButton = {
            TextButton(onClick = {
                onSave(
                    ModelConfig(
                        accelerator    = accelerator,
                        maxTokens      = maxTokens,
                        topK           = topK,
                        topP           = topP,
                        temperature    = temperature,
                        enableThinking = enableThinking,
                    )
                )
            }) { Text("OK", color = JarvisPalette.GoldPrimary) }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text("Cancel", color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        },
        containerColor = JarvisPalette.SurfaceElevated,
        shape          = RoundedCornerShape(20.dp),
    )
}

/**
 * Slider + numeric text field on the same row — matches Gallery's layout
 * (label above, current value on the left, slider filling the remaining
 * width, then a compact editable number field on the right). Using the
 * numeric field lets users set exact values without hunting for the
 * right pixel on the slider.
 */
@Composable
private fun SliderRow(
    label:    String,
    value:    Float,
    range:    ClosedFloatingPointRange<Float>,
    onChange: (Float) -> Unit,
    format:   (Float) -> String,
) {
    Text(label, style = MaterialTheme.typography.labelMedium)
    Spacer(Modifier.height(4.dp))
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(
            format(range.start),
            style    = MaterialTheme.typography.bodySmall,
            color    = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.width(56.dp),
        )
        Slider(
            value         = value,
            onValueChange = onChange,
            valueRange    = range,
            modifier      = Modifier.weight(1f),
        )
        Spacer(Modifier.width(8.dp))
        OutlinedTextField(
            value         = format(value),
            onValueChange = { typed ->
                val parsed = typed.toFloatOrNull()
                if (parsed != null && parsed in range) onChange(parsed)
            },
            singleLine    = true,
            modifier      = Modifier.width(88.dp),
        )
    }
}
