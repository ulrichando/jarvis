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
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Security
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch

/**
 * Two-page onboarding flow shown on first launch.
 *
 * Page 0 — Welcome
 * Page 1 — Permissions overview + "Open Permissions" CTA
 *
 * API keys and brain server URLs are configured from the in-app Settings
 * screen instead of onboarding — most users already have Groq set up from
 * a previous install, and forcing a mandatory key page blocked first-time
 * exploration of the UI.
 *
 * When the user completes the flow, [onFinish] is called — the NavGraph
 * replaces this destination with the Chat screen and sets `onboardingDone`
 * in SharedPreferences so it never shows again.
 */
@Composable
fun OnboardingScreen(
    onFinish:      () -> Unit,
    onPermissions: () -> Unit,
) {
    val pagerState = rememberPagerState(pageCount = { 2 })
    val scope      = rememberCoroutineScope()

    fun nextPage() = scope.launch {
        if (pagerState.currentPage < 1) pagerState.animateScrollToPage(pagerState.currentPage + 1)
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
                1 -> PermissionsPage(
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
            repeat(2) { i ->
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
            "Your personal AI command-and-control center.\nFull root access · Anthropic AI · Zero cloud lock-in.",
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
