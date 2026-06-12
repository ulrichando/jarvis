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
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
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
import com.jarvis.android.domain.model.CloudModel
import com.jarvis.android.domain.model.CloudProvider

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
            // Unified provider card. Pick any backend — Anthropic, OpenAI,
            // Groq, DeepSeek, xAI, OpenRouter, Mistral, or JARVIS Brain — and
            // configure just that one. Multiple providers can be configured at
            // the same time; switch live from the chat top-bar picker. There
            // is no longer a "Connection mode" gate above this card; Brain is
            // a peer in the dropdown like any other provider.
            SectionTitle("Provider")
            DirectCloudCard(state, viewModel)

            Spacer(Modifier.height(16.dp))

            if (state.directProvider == CloudProvider.ANTHROPIC) {
                SectionTitle("Anthropic Endpoint")
                EndpointCard(state, viewModel)
                Spacer(Modifier.height(16.dp))
            }

            // HuggingFace token — used for gated model downloads (Gemma, Llama, …).
            // Independent of connection mode — local models work in both modes.
            SectionTitle("HuggingFace Token")
            HfTokenCard(state, viewModel)

            Spacer(Modifier.height(16.dp))

            // Voice section — always visible regardless of which cloud
            // provider is active, because Edge TTS works standalone and
            // users most often switch here specifically to change the
            // voice (see: user was on Groq and couldn't find the picker
            // because it was nested in the Brain-only card).
            SectionTitle("Voice")
            VoiceCard(state, viewModel)

            Spacer(Modifier.height(16.dp))

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

/**
 * Provider dropdown + model dropdown + single API-key input that swaps its
 * contents as the selected provider changes. Only providers with OpenAI-compat
 * HTTP (OpenAI, Groq, DeepSeek, xAI, OpenRouter, Mistral) plus Anthropic are
 * listed. Google is omitted until the non-OpenAI Gemini path is wired.
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
private fun DirectCloudCard(state: SettingsUiState, viewModel: SettingsViewModel) {
    val supported = remember {
        listOf(
            CloudProvider.ANTHROPIC,
            CloudProvider.OPENAI,
            CloudProvider.GROQ,
            CloudProvider.DEEPSEEK,
            CloudProvider.XAI,
            CloudProvider.OPENROUTER,
            CloudProvider.MISTRAL,
            CloudProvider.JARVIS_BRAIN,
        )
    }
    var providerMenuOpen by remember { mutableStateOf(false) }
    var modelMenuOpen    by remember { mutableStateOf(false) }
    var showKey          by remember { mutableStateOf(false) }

    val catalogForProvider = remember(state.directProvider) {
        CloudModel.CATALOG.filter { it.provider == state.directProvider }
    }

    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp)) {
            // ── Provider dropdown (clickable-row pattern) ─────────────────
            // ExposedDropdownMenuBox + readOnly TextField is flaky on some
            // Samsung OneUI builds — the IME intercepts the tap and the menu
            // never opens. A plain clickable Row + DropdownMenu is bulletproof.
            Text(
                "Provider",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.primary,
            )
            Spacer(Modifier.height(4.dp))
            Box {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable { providerMenuOpen = true }
                        .padding(vertical = 12.dp, horizontal = 4.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    Text(state.directProvider.displayName, style = MaterialTheme.typography.bodyLarge)
                    Icon(Icons.Default.ArrowDropDown, contentDescription = "Open provider menu")
                }
                DropdownMenu(
                    expanded         = providerMenuOpen,
                    onDismissRequest = { providerMenuOpen = false },
                ) {
                    supported.forEach { p ->
                        DropdownMenuItem(
                            text    = { Text(p.displayName) },
                            onClick = {
                                viewModel.onIntent(SettingsIntent.SelectDirectProvider(p))
                                providerMenuOpen = false
                            },
                        )
                    }
                }
            }

            Spacer(Modifier.height(12.dp))

            // ── Model dropdown ────────────────────────────────────────────
            if (catalogForProvider.isNotEmpty()) {
                val currentModel = catalogForProvider.firstOrNull { it.id == state.directModel }
                Text(
                    "Model",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.primary,
                )
                Spacer(Modifier.height(4.dp))
                Box {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clickable { modelMenuOpen = true }
                            .padding(vertical = 12.dp, horizontal = 4.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Column(Modifier.weight(1f)) {
                            Text(currentModel?.label ?: state.directModel, style = MaterialTheme.typography.bodyLarge)
                            currentModel?.description?.let {
                                Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                        Icon(Icons.Default.ArrowDropDown, contentDescription = "Open model menu")
                    }
                    DropdownMenu(
                        expanded         = modelMenuOpen,
                        onDismissRequest = { modelMenuOpen = false },
                    ) {
                        catalogForProvider.forEach { m ->
                            DropdownMenuItem(
                                text    = { Column { Text(m.label); Text(m.description, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) } },
                                onClick = {
                                    viewModel.onIntent(SettingsIntent.SelectDirectModel(m.id))
                                    modelMenuOpen = false
                                },
                            )
                        }
                    }
                }
                Spacer(Modifier.height(12.dp))
            }

            if (state.directProvider == CloudProvider.JARVIS_BRAIN) {
                // Brain provider — replace the API-key field with the brain
                // server URL field + the existing brain-provider chips. The
                // pref slot is `brain_server_url` (same one Brain mode used
                // before the Connection toggle was removed) so existing setups
                // keep working without migration.
                Text(
                    "Brain server URL — your homelab JARVIS instance proxies all requests, picks the upstream provider, and streams the response back.",
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
                    enabled  = state.brainServerUrl.isNotBlank(),
                    modifier = Modifier.fillMaxWidth(),
                ) { Text("Save brain server") }

                Spacer(Modifier.height(16.dp))
                HorizontalDivider()
                Spacer(Modifier.height(12.dp))
                Text(
                    "Voice (TTS) — optional. Point this at the brain's /tts server (default port 8766) and voice mode will speak with the same voice your computer uses, instead of Android's local TTS. Leave blank to use Android TTS.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(
                    value         = state.brainTtsUrl,
                    onValueChange = { viewModel.onIntent(SettingsIntent.SetBrainTtsUrl(it)) },
                    label         = { Text("TTS server URL") },
                    placeholder   = { Text("http://10.10.0.50:8766") },
                    singleLine    = true,
                    modifier      = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(8.dp))
                Button(
                    onClick  = { viewModel.onIntent(SettingsIntent.SaveBrainTtsUrl) },
                    modifier = Modifier.fillMaxWidth(),
                ) { Text("Save TTS server") }

                if (state.brainProviders.isNotEmpty() || state.isLoadingProviders) {
                    Spacer(Modifier.height(16.dp))
                    HorizontalDivider()
                    Spacer(Modifier.height(12.dp))
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                        Text(
                            "Active brain provider",
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
                    androidx.compose.foundation.layout.FlowRow(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
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
            } else {
                // ── API key for selected cloud provider ───────────────────
                if (state.hasDirectKey) {
                    Text(
                        "Stored key: ${state.directKeyMasked}",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.height(8.dp))
                }

                OutlinedTextField(
                    value         = state.directProviderKey,
                    onValueChange = { viewModel.onIntent(SettingsIntent.SetDirectProviderKey(it)) },
                    label         = { Text(if (state.hasDirectKey) "Replace ${state.directProvider.displayName} key" else "${state.directProvider.displayName} API key") },
                    singleLine    = true,
                    visualTransformation = if (showKey) VisualTransformation.None else PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                    trailingIcon = {
                        IconButton(onClick = { showKey = !showKey }) {
                            Icon(
                                if (showKey) Icons.Default.VisibilityOff else Icons.Default.Visibility,
                                contentDescription = if (showKey) "Hide" else "Show",
                            )
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                )

                Spacer(Modifier.height(12.dp))

                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(
                        onClick  = { viewModel.onIntent(SettingsIntent.SaveDirectProviderKey) },
                        enabled  = state.directProviderKey.isNotBlank() && !state.isSaving,
                        modifier = Modifier.weight(1f),
                    ) { Text("Save") }
                    if (state.hasDirectKey) {
                        OutlinedButton(
                            onClick  = { viewModel.onIntent(SettingsIntent.ClearDirectProviderKey) },
                            modifier = Modifier.weight(1f),
                        ) { Text("Clear") }
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
private fun HfTokenCard(state: SettingsUiState, viewModel: SettingsViewModel) {
    var showToken by remember { mutableStateOf(false) }

    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp)) {
            Text(
                "Required for gated models (Gemma, Llama, some Mistral variants). " +
                    "Create a read token at huggingface.co/settings/tokens after accepting the model license.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(8.dp))

            if (state.hasHfToken) {
                Text(
                    "Stored token: ${state.hfTokenMasked}",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(8.dp))
            }

            OutlinedTextField(
                value         = state.hfToken,
                onValueChange = { viewModel.onIntent(SettingsIntent.SetHfToken(it)) },
                label         = { Text(if (state.hasHfToken) "Replace HF token" else "HuggingFace token") },
                placeholder   = { Text("hf_…") },
                singleLine    = true,
                modifier      = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                visualTransformation = if (showToken) VisualTransformation.None
                                       else PasswordVisualTransformation(),
                trailingIcon = {
                    IconButton(onClick = { showToken = !showToken }) {
                        Icon(
                            if (showToken) Icons.Default.VisibilityOff else Icons.Default.Visibility,
                            if (showToken) "Hide" else "Show",
                        )
                    }
                },
            )

            Spacer(Modifier.height(12.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(
                    onClick  = { viewModel.onIntent(SettingsIntent.SaveHfToken) },
                    enabled  = state.hfToken.isNotBlank(),
                    modifier = Modifier.weight(1f),
                ) {
                    Text("Save")
                }
                if (state.hasHfToken) {
                    OutlinedButton(
                        onClick = { viewModel.onIntent(SettingsIntent.ClearHfToken) },
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
                "Select the Anthropic API endpoint used for all requests.",
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

/**
 * Top-level Voice card, always visible regardless of which cloud provider
 * or connection mode the user has active. Holds the Edge TTS voice picker;
 * easy to extend with pitch / rate / volume controls later.
 */
@Composable
private fun VoiceCard(state: SettingsUiState, viewModel: SettingsViewModel) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp)) {
            // Mirror JarvisTtsEngine.resolveBackend() exactly so the UI's
            // "active" marker never lies about what you'll actually hear.
            // Order: BRAIN → GROQ → EDGE → LOCAL.
            val isActiveBrain = state.brainTtsUrl.isNotBlank()
            val isActiveGroq  = !isActiveBrain &&
                state.hasGroqKey && state.groqTtsEnabled
            val isActiveEdge  = !isActiveBrain && !isActiveGroq &&
                state.edgeTtsEnabled

            val activeLabel = when {
                isActiveBrain ->
                    "Brain TTS (homelab — ${state.brainTtsUrl})"
                isActiveGroq ->
                    "Groq PlayAI (${GroqTtsVoice.labelFor(state.groqTtsVoice)})"
                isActiveEdge ->
                    "Edge TTS (${EdgeTtsVoice.labelFor(state.edgeTtsVoice)})"
                else ->
                    "Android local TTS (built-in)"
            }
            // Short name of the backend that's currently winning — used in
            // the "override" hint shown on inactive voice pickers.
            val activeShort = when {
                isActiveBrain -> "Brain TTS"
                isActiveGroq  -> "Groq"
                isActiveEdge  -> "Edge"
                else          -> "Android built-in"
            }
            Text(
                "Active voice: $activeLabel",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.primary,
            )
            Spacer(Modifier.height(12.dp))
            Text(
                "Voice provider priority: Brain TTS (if URL set) → Groq (if key + enabled) → Edge (if enabled) → Android built-in. " +
                "Groq uses the same API key as chat and is the recommended primary path.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            // ── Groq PlayAI TTS (primary) ─────────────────────────────────
            Spacer(Modifier.height(20.dp))
            HorizontalDivider()
            Spacer(Modifier.height(12.dp))
            Text(
                "Groq PlayAI TTS",
                style = MaterialTheme.typography.titleSmall,
            )
            Spacer(Modifier.height(4.dp))
            if (!state.hasGroqKey) {
                Text(
                    "Requires a Groq API key — set one in the cloud provider section above to enable.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            } else {
                Row(
                    verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
                    modifier          = Modifier.fillMaxWidth(),
                ) {
                    Column(Modifier.weight(1f)) {
                        Text(
                            "Use Groq PlayAI",
                            style = MaterialTheme.typography.bodyMedium,
                        )
                        Text(
                            if (state.groqTtsEnabled)
                                "Using ${GroqTtsVoice.labelFor(state.groqTtsVoice)} — neural PlayAI voice"
                            else
                                "Off — falls back to Android local TTS",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    androidx.compose.material3.Switch(
                        checked         = state.groqTtsEnabled,
                        onCheckedChange = {
                            viewModel.onIntent(SettingsIntent.SetGroqTtsEnabled(it))
                        },
                    )
                }
                if (state.groqTtsEnabled) {
                    Spacer(Modifier.height(12.dp))
                    GroqVoicePicker(
                        selected  = state.groqTtsVoice,
                        onPick    = { viewModel.onIntent(SettingsIntent.SelectGroqTtsVoice(it)) },
                        onPreview = { viewModel.onIntent(SettingsIntent.PreviewGroqVoice(it)) },
                    )
                    if (!isActiveGroq) {
                        Spacer(Modifier.height(8.dp))
                        Text(
                            "Not currently used — $activeShort is serving audio. " +
                            (if (isActiveBrain) "Clear the Brain TTS URL below to switch to Groq."
                             else                "Enable Groq above or disable the other backends to hear this voice."),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.error,
                        )
                    }
                }
            }

            // ── Edge TTS (secondary / opt-in fallback) ─────────────────────
            Spacer(Modifier.height(20.dp))
            HorizontalDivider()
            Spacer(Modifier.height(12.dp))
            Text(
                "Edge TTS (opt-in fallback)",
                style = MaterialTheme.typography.titleSmall,
            )
            Spacer(Modifier.height(4.dp))
            Text(
                "Microsoft's free Edge Read-Aloud voices. Off by default — the " +
                "endpoint rotates anti-abuse tokens aggressively and 403s often.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(8.dp))
            Row(
                verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
                modifier          = Modifier.fillMaxWidth(),
            ) {
                Column(Modifier.weight(1f)) {
                    Text(
                        "Use Edge TTS",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                    Text(
                        if (state.edgeTtsEnabled)
                            "Using ${EdgeTtsVoice.labelFor(state.edgeTtsVoice)}"
                        else
                            "Off",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                androidx.compose.material3.Switch(
                    checked         = state.edgeTtsEnabled,
                    onCheckedChange = {
                        viewModel.onIntent(SettingsIntent.SetEdgeTtsEnabled(it))
                    },
                )
            }
            if (state.edgeTtsEnabled) {
                Spacer(Modifier.height(12.dp))
                EdgeVoicePicker(
                    selected  = state.edgeTtsVoice,
                    onPick    = { viewModel.onIntent(SettingsIntent.SelectEdgeTtsVoice(it)) },
                    onPreview = { viewModel.onIntent(SettingsIntent.PreviewEdgeVoice(it)) },
                )
                if (!isActiveEdge) {
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "Not currently used — $activeShort is serving audio. " +
                        (if (isActiveBrain) "Clear the Brain TTS URL below to fall through to Edge."
                         else if (isActiveGroq)  "Disable Groq above to fall through to Edge."
                         else                    "Enable Edge to hear this voice."),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            }
        }
    }
}

/**
 * Mirror of [EdgeVoicePicker] for Groq PlayAI voices. Same layout, same
 * UX — just a different catalog so the user picks from the PlayAI set.
 */
@OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
@Composable
private fun GroqVoicePicker(
    selected:  String,
    onPick:    (String) -> Unit,
    onPreview: (String) -> Unit,
) {
    var expanded by androidx.compose.runtime.remember { androidx.compose.runtime.mutableStateOf(false) }
    val active      = GroqTtsVoice.CATALOG.firstOrNull { it.id == selected }
    val activeLabel = active?.label ?: selected
    Column(Modifier.fillMaxWidth()) {
        ExposedDropdownMenuBox(
            expanded         = expanded,
            onExpandedChange = { expanded = it },
        ) {
            OutlinedTextField(
                value          = activeLabel,
                onValueChange  = {},
                readOnly       = true,
                label          = { Text("Voice") },
                supportingText = active?.let { { Text(it.description) } },
                trailingIcon   = { ExposedDropdownMenuDefaults.TrailingIcon(expanded) },
                modifier       = Modifier
                    .menuAnchor(MenuAnchorType.PrimaryNotEditable)
                    .fillMaxWidth(),
            )
            ExposedDropdownMenu(
                expanded         = expanded,
                onDismissRequest = { expanded = false },
            ) {
                GroqTtsVoice.CATALOG.forEach { voice ->
                    DropdownMenuItem(
                        text    = {
                            Column {
                                Text(voice.label)
                                Text(
                                    voice.description,
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        },
                        trailingIcon = {
                            IconButton(onClick = { onPreview(voice.id) }) {
                                Icon(
                                    imageVector        = Icons.Default.PlayArrow,
                                    contentDescription = "Preview",
                                )
                            }
                        },
                        onClick = {
                            onPick(voice.id)
                            // Auto-preview on selection — same pattern as
                            // ChatGPT / Claude / Siri. Gives instant audible
                            // confirmation the voice actually changed.
                            onPreview(voice.id)
                            expanded = false
                        },
                    )
                }
            }
        }
        Spacer(Modifier.height(8.dp))
        androidx.compose.material3.TextButton(onClick = { onPreview(selected) }) {
            Icon(
                imageVector        = Icons.Default.PlayArrow,
                contentDescription = null,
                modifier           = Modifier.size(18.dp),
            )
            Spacer(Modifier.size(6.dp))
            Text("Preview current voice")
        }
    }
}

/**
 * Dropdown picker for the Edge TTS voice. Shows the friendly label + a short
 * one-line description so the user can tell the voices apart without hearing
 * each one first. Males are grouped above females in [EdgeTtsVoice.CATALOG]
 * so the JARVIS-tone defaults are at the top.
 */
@OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)
@Composable
private fun EdgeVoicePicker(
    selected:  String,
    onPick:    (String) -> Unit,
    onPreview: (String) -> Unit,
) {
    var expanded by androidx.compose.runtime.remember { androidx.compose.runtime.mutableStateOf(false) }
    val active = EdgeTtsVoice.CATALOG.firstOrNull { it.id == selected }
    val activeLabel = active?.label ?: selected
    Column(Modifier.fillMaxWidth()) {
        ExposedDropdownMenuBox(
            expanded         = expanded,
            onExpandedChange = { expanded = it },
        ) {
            OutlinedTextField(
                value         = activeLabel,
                onValueChange = {},
                readOnly      = true,
                label         = { Text("Voice") },
                supportingText = active?.let { { Text(it.description) } },
                trailingIcon  = { ExposedDropdownMenuDefaults.TrailingIcon(expanded) },
                modifier      = Modifier
                    .menuAnchor(MenuAnchorType.PrimaryNotEditable)
                    .fillMaxWidth(),
            )
            ExposedDropdownMenu(
                expanded         = expanded,
                onDismissRequest = { expanded = false },
            ) {
                EdgeTtsVoice.CATALOG.forEach { voice ->
                    DropdownMenuItem(
                        text    = {
                            Column {
                                Text(voice.label)
                                Text(
                                    voice.description,
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        },
                        trailingIcon = {
                            IconButton(onClick = { onPreview(voice.id) }) {
                                Icon(
                                    imageVector        = Icons.Default.PlayArrow,
                                    contentDescription = "Preview",
                                )
                            }
                        },
                        onClick = {
                            onPick(voice.id)
                            onPreview(voice.id)
                            expanded = false
                        },
                    )
                }
            }
        }
        Spacer(Modifier.height(8.dp))
        androidx.compose.material3.TextButton(onClick = { onPreview(selected) }) {
            Icon(
                imageVector        = Icons.Default.PlayArrow,
                contentDescription = null,
                modifier           = Modifier.size(18.dp),
            )
            Spacer(Modifier.size(6.dp))
            Text("Preview current voice")
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
