package com.jarvis.android.presentation.onboarding

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Security
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.jarvis.android.presentation.settings.SettingsIntent
import com.jarvis.android.presentation.settings.SettingsViewModel
import kotlinx.coroutines.launch

/**
 * Three-page onboarding flow shown on first launch.
 *
 * Page 0 — Welcome
 * Page 1 — API Key entry
 * Page 2 — Permissions overview + "Open Permissions" CTA
 *
 * When the user completes the flow, [onFinish] is called — the NavGraph
 * replaces this destination with the Chat screen and sets `onboardingDone`
 * in SharedPreferences so it never shows again.
 */
@Composable
fun OnboardingScreen(
    onFinish:      () -> Unit,
    onPermissions: () -> Unit,
    viewModel:     SettingsViewModel = hiltViewModel(),
) {
    val state      by viewModel.uiState.collectAsState()
    val pagerState  = rememberPagerState(pageCount = { 3 })
    val scope       = rememberCoroutineScope()

    fun nextPage() = scope.launch {
        if (pagerState.currentPage < 2) pagerState.animateScrollToPage(pagerState.currentPage + 1)
        else onFinish()
    }

    Column(Modifier.fillMaxSize()) {
        HorizontalPager(
            state    = pagerState,
            modifier = Modifier.weight(1f),
            userScrollEnabled = false,
        ) { page ->
            when (page) {
                0 -> WelcomePage(onNext = { nextPage() })
                1 -> ConnectionSetupPage(state, viewModel, onNext = {
                    when (state.connectionMode) {
                        "brain" -> viewModel.onIntent(SettingsIntent.SaveBrainSettings)
                        else    -> if (state.apiKey.isNotBlank()) viewModel.onIntent(SettingsIntent.SaveApiKey)
                    }
                    nextPage()
                })
                2 -> PermissionsPage(
                    onOpenPermissions = onPermissions,
                    onFinish          = onFinish,
                )
            }
        }

        // Page indicator dots
        Row(
            Modifier
                .fillMaxWidth()
                .padding(bottom = 16.dp),
            horizontalArrangement = Arrangement.Center,
        ) {
            repeat(3) { i ->
                val selected = pagerState.currentPage == i
                Text(
                    text  = if (selected) "●" else "○",
                    color = if (selected) MaterialTheme.colorScheme.primary
                            else MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 4.dp),
                )
            }
        }
    }
}

@Composable
private fun WelcomePage(onNext: () -> Unit) {
    Column(
        modifier              = Modifier.fillMaxSize().padding(32.dp),
        verticalArrangement   = Arrangement.Center,
        horizontalAlignment   = Alignment.CenterHorizontally,
    ) {
        Icon(
            Icons.Default.Security,
            contentDescription = null,
            tint     = MaterialTheme.colorScheme.primary,
            modifier = Modifier.size(72.dp),
        )
        Spacer(Modifier.height(24.dp))
        Text(
            "JARVIS",
            style     = MaterialTheme.typography.displaySmall,
            textAlign = TextAlign.Center,
        )
        Spacer(Modifier.height(8.dp))
        Text(
            "Your personal AI command-and-control center.\nFull root access · Claude AI · Zero cloud lock-in.",
            style     = MaterialTheme.typography.bodyMedium,
            textAlign = TextAlign.Center,
            color     = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(48.dp))
        Button(onClick = onNext, modifier = Modifier.fillMaxWidth()) {
            Text("Get Started")
        }
    }
}

@Composable
private fun ConnectionSetupPage(
    state:     com.jarvis.android.presentation.settings.SettingsUiState,
    viewModel: SettingsViewModel,
    onNext:    () -> Unit,
) {
    var showKey by remember { mutableStateOf(false) }
    val isBrain = state.connectionMode == "brain"

    Column(
        modifier              = Modifier.fillMaxSize().padding(32.dp),
        verticalArrangement   = Arrangement.Center,
        horizontalAlignment   = Alignment.CenterHorizontally,
    ) {
        Text("Connect to AI", style = MaterialTheme.typography.headlineSmall, textAlign = TextAlign.Center)
        Spacer(Modifier.height(8.dp))
        Text(
            "Choose how JARVIS reaches its brain.",
            style     = MaterialTheme.typography.bodyMedium,
            textAlign = TextAlign.Center,
            color     = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(24.dp))

        // Mode selector
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            FilterChip(
                selected = !isBrain,
                onClick  = { viewModel.onIntent(SettingsIntent.SetConnectionMode("anthropic")) },
                label    = { Text("Anthropic API") },
                modifier = Modifier.weight(1f),
            )
            FilterChip(
                selected = isBrain,
                onClick  = { viewModel.onIntent(SettingsIntent.SetConnectionMode("brain")) },
                label    = { Text("JARVIS Brain") },
                modifier = Modifier.weight(1f),
            )
        }

        Spacer(Modifier.height(24.dp))

        if (isBrain) {
            Text(
                "Enter your JARVIS server URL. The brain runs all AI processing — no API key needed on the phone.",
                style     = MaterialTheme.typography.bodySmall,
                textAlign = TextAlign.Center,
                color     = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(16.dp))
            OutlinedTextField(
                value         = state.brainServerUrl,
                onValueChange = { viewModel.onIntent(SettingsIntent.SetBrainServerUrl(it)) },
                label         = { Text("Brain server URL") },
                placeholder   = { Text("http://10.10.0.50:8765") },
                singleLine    = true,
                modifier      = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(24.dp))
            Button(
                onClick  = onNext,
                modifier = Modifier.fillMaxWidth(),
                enabled  = state.brainServerUrl.isNotBlank(),
            ) {
                Text("Save & Continue")
            }
        } else {
            Text(
                "Enter your Anthropic API key. Stored encrypted on-device, never leaves your phone.",
                style     = MaterialTheme.typography.bodySmall,
                textAlign = TextAlign.Center,
                color     = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(16.dp))
            OutlinedTextField(
                value         = state.apiKey,
                onValueChange = { viewModel.onIntent(SettingsIntent.SetApiKey(it)) },
                label         = { Text("Anthropic API key") },
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
            Spacer(Modifier.height(24.dp))
            Button(
                onClick  = onNext,
                modifier = Modifier.fillMaxWidth(),
                enabled  = state.apiKey.isNotBlank(),
            ) {
                Text("Save & Continue")
            }
        }
        Spacer(Modifier.height(8.dp))
        TextButton(onClick = onNext) {
            Text("Skip for now", color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun PermissionsPage(
    onOpenPermissions: () -> Unit,
    onFinish:          () -> Unit,
) {
    Column(
        modifier              = Modifier.fillMaxSize().padding(32.dp),
        verticalArrangement   = Arrangement.Center,
        horizontalAlignment   = Alignment.CenterHorizontally,
    ) {
        Text("Grant Permissions", style = MaterialTheme.typography.headlineSmall, textAlign = TextAlign.Center)
        Spacer(Modifier.height(8.dp))
        Text(
            "JARVIS needs various permissions to control your device.\n\n" +
            "• Camera & Microphone — voice/vision commands\n" +
            "• Location — GPS tools\n" +
            "• Notification Listener — read all notifications\n" +
            "• Accessibility Service — interact with any screen\n" +
            "• Battery Optimization Exempt — stay alive in background\n\n" +
            "You can manage all permissions from Settings at any time.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(32.dp))
        Button(onClick = onOpenPermissions, modifier = Modifier.fillMaxWidth()) {
            Text("Review Permissions")
        }
        Spacer(Modifier.height(8.dp))
        TextButton(onClick = onFinish) {
            Text("Skip — set up later", color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}
