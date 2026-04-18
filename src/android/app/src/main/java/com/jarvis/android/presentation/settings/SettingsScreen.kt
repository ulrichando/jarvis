package com.jarvis.android.presentation.settings

import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.MenuAnchorType
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    onBack:    () -> Unit = {},
    viewModel: SettingsViewModel = hiltViewModel(),
) {
    val state   by viewModel.uiState.collectAsState()
    val snackbar = remember { SnackbarHostState() }

    LaunchedEffect(state.savedMessage, state.error) {
        state.savedMessage?.let { snackbar.showSnackbar(it); viewModel.onIntent(SettingsIntent.DismissMessage) }
        state.error?.let        { snackbar.showSnackbar(it); viewModel.onIntent(SettingsIntent.DismissMessage) }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            TopAppBar(
                title = { Text("Settings") },
                navigationIcon = {
                    IconButton(onClick = onBack) { Icon(Icons.Default.ArrowBack, "Back") }
                },
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .padding(padding)
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
        ) {
            // Connection mode
            SectionTitle("Connection")
            ConnectionModeCard(state, viewModel)

            Spacer(Modifier.height(16.dp))

            if (state.connectionMode == "anthropic") {
                // API Key section
                SectionTitle("Claude API Key")
                ApiKeyCard(state, viewModel)

                Spacer(Modifier.height(16.dp))

                // Endpoint section
                SectionTitle("Endpoint")
                EndpointCard(state, viewModel)

                Spacer(Modifier.height(16.dp))
            }

            // About section
            SectionTitle("About")
            AboutCard()
        }
    }
}

@Composable
private fun SectionTitle(title: String) {
    Text(
        title,
        style    = MaterialTheme.typography.labelMedium,
        color    = MaterialTheme.colorScheme.primary,
        modifier = Modifier.padding(bottom = 6.dp),
    )
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ConnectionModeCard(state: SettingsUiState, viewModel: SettingsViewModel) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp)) {
            Text(
                "Choose how JARVIS connects to AI.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(12.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilterChip(
                    selected = state.connectionMode == "anthropic",
                    onClick  = { viewModel.onIntent(SettingsIntent.SetConnectionMode("anthropic")) },
                    label    = { Text("Anthropic API") },
                    modifier = Modifier.weight(1f),
                )
                FilterChip(
                    selected = state.connectionMode == "brain",
                    onClick  = { viewModel.onIntent(SettingsIntent.SetConnectionMode("brain")) },
                    label    = { Text("JARVIS Brain") },
                    modifier = Modifier.weight(1f),
                )
            }

            if (state.connectionMode == "brain") {
                Spacer(Modifier.height(12.dp))
                Text(
                    "Enter your JARVIS server address. The brain handles all AI processing — no API key needed.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(
                    value         = state.brainServerUrl,
                    onValueChange = { viewModel.onIntent(SettingsIntent.SetBrainServerUrl(it)) },
                    label         = { Text("Brain server URL") },
                    placeholder   = { Text("http://10.10.0.50:8765") },
                    singleLine    = true,
                    modifier      = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(12.dp))
                Button(
                    onClick  = { viewModel.onIntent(SettingsIntent.SaveBrainSettings) },
                    modifier = Modifier.fillMaxWidth(),
                    enabled  = state.brainServerUrl.isNotBlank(),
                ) {
                    Text("Save Brain Server")
                }

                // Provider selection
                Spacer(Modifier.height(16.dp))
                HorizontalDivider()
                Spacer(Modifier.height(12.dp))
                Row(
                    Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    Text(
                        "AI Provider",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.primary,
                    )
                    IconButton(
                        onClick  = { viewModel.onIntent(SettingsIntent.RefreshBrainProviders) },
                        modifier = Modifier.size(32.dp),
                    ) {
                        if (state.isLoadingProviders) {
                            CircularProgressIndicator(modifier = Modifier.size(16.dp), strokeWidth = 2.dp)
                        } else {
                            Icon(Icons.Default.Refresh, contentDescription = "Refresh providers", modifier = Modifier.size(18.dp))
                        }
                    }
                }
                Spacer(Modifier.height(8.dp))
                Text(
                    "Select which AI provider the brain server uses.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(8.dp))
                FlowRow(
                    Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    // "Auto" chip — clears the pin
                    FilterChip(
                        selected = state.brainPinnedProvider.isEmpty(),
                        onClick  = { viewModel.onIntent(SettingsIntent.PinBrainProvider("")) },
                        label    = { Text("Auto") },
                    )
                    state.brainProviders.forEach { provider ->
                        FilterChip(
                            selected = state.brainPinnedProvider == provider.name,
                            onClick  = { viewModel.onIntent(SettingsIntent.PinBrainProvider(provider.name)) },
                            label    = { Text(provider.name) },
                        )
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ApiKeyCard(state: SettingsUiState, viewModel: SettingsViewModel) {
    var showKey by remember { mutableStateOf(false) }

    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp)) {
            if (state.hasApiKey) {
                Text(
                    "Stored key: ${state.apiKeyMasked}",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(8.dp))
            }

            OutlinedTextField(
                value         = state.apiKey,
                onValueChange = { viewModel.onIntent(SettingsIntent.SetApiKey(it)) },
                label         = { Text(if (state.hasApiKey) "Replace API key" else "Anthropic API key") },
                placeholder   = { Text("sk-ant-api03-…") },
                singleLine    = true,
                modifier      = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                visualTransformation = if (showKey) VisualTransformation.None
                                       else PasswordVisualTransformation(),
                trailingIcon = {
                    IconButton(onClick = { showKey = !showKey }) {
                        Icon(
                            if (showKey) Icons.Default.VisibilityOff else Icons.Default.Visibility,
                            if (showKey) "Hide" else "Show",
                        )
                    }
                },
            )

            Spacer(Modifier.height(12.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(
                    onClick  = { viewModel.onIntent(SettingsIntent.SaveApiKey) },
                    enabled  = !state.isSaving,
                    modifier = Modifier.weight(1f),
                ) {
                    Text(if (state.isSaving) "Saving…" else "Save")
                }
                if (state.hasApiKey) {
                    OutlinedButton(
                        onClick = { viewModel.onIntent(SettingsIntent.ClearApiKey) },
                        colors  = ButtonDefaults.outlinedButtonColors(
                            contentColor = MaterialTheme.colorScheme.error,
                        ),
                        modifier = Modifier.weight(1f),
                    ) { Text("Clear") }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun EndpointCard(state: SettingsUiState, viewModel: SettingsViewModel) {
    var expanded by remember { mutableStateOf(false) }

    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp)) {
            Text(
                "Select the Claude API endpoint used for all requests.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(8.dp))
            ExposedDropdownMenuBox(
                expanded         = expanded,
                onExpandedChange = { expanded = it },
            ) {
                OutlinedTextField(
                    value         = state.activeEndpoint,
                    onValueChange = {},
                    readOnly      = true,
                    label         = { Text("Endpoint") },
                    trailingIcon  = { ExposedDropdownMenuDefaults.TrailingIcon(expanded) },
                    modifier      = Modifier
                        .menuAnchor(MenuAnchorType.PrimaryNotEditable)
                        .fillMaxWidth(),
                )
                ExposedDropdownMenu(
                    expanded         = expanded,
                    onDismissRequest = { expanded = false },
                ) {
                    state.endpointOptions.forEach { option ->
                        DropdownMenuItem(
                            text    = { Text(option) },
                            onClick = {
                                viewModel.onIntent(SettingsIntent.SetEndpoint(option))
                                expanded = false
                            },
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun AboutCard() {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp)) {
            AboutRow("App",     "JARVIS Android Client")
            AboutRow("Model",   "claude-opus-4-5 / claude-sonnet-4-5")
            AboutRow("Backend", "Anthropic Messages API (SSE)")
            AboutRow("Root",    "Magisk / KernelSU via libsu 5.3.0")
        }
    }
}

@Composable
private fun AboutRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 3.dp)) {
        Text(
            label,
            style    = MaterialTheme.typography.bodySmall,
            color    = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.weight(1f),
        )
        Text(value, style = MaterialTheme.typography.bodySmall)
    }
}
