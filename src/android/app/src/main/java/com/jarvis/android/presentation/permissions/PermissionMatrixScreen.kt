package com.jarvis.android.presentation.permissions

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Build
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.Cable
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import com.jarvis.android.system.adb.AdbState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.jarvis.android.core.designsystem.JarvisPalette
import com.jarvis.android.presentation.components.PermissionRow
import com.jarvis.android.system.permissions.PermissionEntry
import com.jarvis.android.system.permissions.PermissionStatus
import com.jarvis.android.system.permissions.PermissionTier

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PermissionMatrixScreen(
    onBack:           () -> Unit = {},
    onInstallRoot:    () -> Unit = {},
    viewModel:        PermissionViewModel = hiltViewModel(),
) {
    val entries        by viewModel.permissions.collectAsState()
    val autoGrantMode  by viewModel.autoGrantMode.collectAsState()
    val rootGranting   by viewModel.rootGranting.collectAsState()
    val adbState       by viewModel.adbState.collectAsState()
    val lifecycleOwner  = LocalLifecycleOwner.current

    // rememberUpdatedState so the DisposableEffect closure always reads the
    // latest autoGrantMode without needing to be re-created.
    val latestAutoGrant = rememberUpdatedState(autoGrantMode)

    // Second launcher exclusively for ACCESS_BACKGROUND_LOCATION.
    // Android 11+ requires background location to be a SEPARATE request that fires
    // only AFTER foreground location (fine/coarse) is already granted. Batching it
    // with other permissions causes an automatic denial.
    val bgLocationLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.RequestMultiplePermissions(),
        onResult = { viewModel.refresh() },
    )

    // Main launcher for all dangerous permissions except background location.
    // After the dialog resolves, check if background location is now eligible
    // (foreground location just granted) and chain into bgLocationLauncher.
    val permissionLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.RequestMultiplePermissions(),
        onResult = {
            viewModel.refresh()
            // Chain: if foreground location was just granted, now request bg location
            val bgManifest = viewModel.getBackgroundLocationManifest(viewModel.permissions.value)
            if (bgManifest != null) bgLocationLauncher.launch(arrayOf(bgManifest))
        },
    )

    // Refresh on every resume.
    // If the auto-grant wizard is running, also advance to the next Settings-based
    // special permission so the user never has to tap "Grant All" again.
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) {
                viewModel.refresh()
                if (latestAutoGrant.value) {
                    viewModel.advanceSpecialGrant(viewModel.permissions.value)
                }
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    val grouped         = viewModel.groupedByTier(entries)
    val (granted, total) = viewModel.summary(entries)

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Permissions") },
                navigationIcon = {
                    IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") }
                },
            )
        },
        bottomBar = {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp),
            ) {
                // ── ADB setup banner (only when neither root nor ADB connected) ──
                if (!viewModel.isRooted && adbState !is AdbState.Connected) {
                    AdbSetupBanner(
                        adbState  = adbState,
                        onConnect = { viewModel.connectAdb() },
                        modifier  = Modifier.padding(bottom = 8.dp),
                    )
                }

                if (granted < total) {
                    when {
                        // ── Strategy 1: Root — fully silent, zero dialogs ────────
                        viewModel.isRooted -> {
                            Button(
                                onClick  = { viewModel.grantAllViaRoot() },
                                enabled  = !rootGranting,
                                modifier = Modifier.fillMaxWidth(),
                            ) {
                                if (rootGranting) {
                                    CircularProgressIndicator(
                                        modifier    = Modifier.size(16.dp),
                                        color       = MaterialTheme.colorScheme.onPrimary,
                                        strokeWidth = 2.dp,
                                    )
                                    Spacer(Modifier.width(8.dp))
                                    Text("Granting…")
                                } else {
                                    Icon(Icons.Default.AutoAwesome, null, Modifier.size(16.dp))
                                    Spacer(Modifier.width(8.dp))
                                    Text("Grant All Automatically (${total - granted} remaining)")
                                }
                            }
                        }
                        // ── Strategy 2: ADB — fully silent, zero dialogs ─────────
                        adbState is AdbState.Connected -> {
                            Button(
                                onClick  = { viewModel.grantAllViaAdb() },
                                enabled  = !rootGranting,
                                modifier = Modifier.fillMaxWidth(),
                            ) {
                                if (rootGranting) {
                                    CircularProgressIndicator(
                                        modifier    = Modifier.size(16.dp),
                                        color       = MaterialTheme.colorScheme.onPrimary,
                                        strokeWidth = 2.dp,
                                    )
                                    Spacer(Modifier.width(8.dp))
                                    Text("Granting via ADB…")
                                } else {
                                    Icon(Icons.Default.Cable, null, Modifier.size(16.dp))
                                    Spacer(Modifier.width(8.dp))
                                    Text("Grant All via ADB (${total - granted} remaining)")
                                }
                            }
                        }
                        // ── Strategy 3: wizard — dialogs + sequential Settings ───
                        else -> {
                            Button(
                                onClick  = {
                                    val dangerous = viewModel.getMissingDangerousManifests(entries)
                                    if (dangerous.isNotEmpty()) {
                                        permissionLauncher.launch(dangerous.toTypedArray())
                                    } else {
                                        val bg = viewModel.getBackgroundLocationManifest(entries)
                                        if (bg != null) bgLocationLauncher.launch(arrayOf(bg))
                                    }
                                    viewModel.grantDialogSpecialPermissions(entries)
                                    viewModel.startAutoGrantMode()
                                    viewModel.advanceSpecialGrant(entries)
                                },
                                modifier = Modifier.fillMaxWidth(),
                            ) {
                                Text("Grant All (${total - granted} remaining)")
                            }
                        }
                    }
                }
            }
        },
    ) { padding ->
        Column(Modifier.padding(padding).fillMaxSize()) {
            // Overall progress bar
            ProgressHeader(granted, total)

            LazyColumn(Modifier.fillMaxSize()) {
                // Render each tier as a section
                PermissionTier.values().forEach { tier ->
                    val tierEntries = grouped[tier] ?: return@forEach

                    item(key = "header_$tier") {
                        TierHeader(tier, tierEntries)
                    }

                    // For ROOT tier: show Install button when not yet rooted
                    if (tier == PermissionTier.ROOT &&
                        tierEntries.all { it.status != PermissionStatus.GRANTED }) {
                        item(key = "root_install_btn") {
                            RootInstallButton(onInstallRoot)
                        }
                    }

                    items(tierEntries, key = { it.id }) { entry ->
                        PermissionRow(
                            entry   = entry,
                            onGrant = { e ->
                                when (e.tier) {
                                    PermissionTier.DANGEROUS -> {
                                        val manifest = e.manifestName
                                        if (!manifest.isNullOrBlank()) {
                                            // Background location must use its own launcher
                                            if (e.id == "bg_location") {
                                                bgLocationLauncher.launch(arrayOf(manifest))
                                            } else {
                                                permissionLauncher.launch(arrayOf(manifest))
                                            }
                                        }
                                    }
                                    else -> viewModel.grant(e)
                                }
                            },
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun ProgressHeader(granted: Int, total: Int) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 8.dp),
    ) {
        Row(
            modifier          = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                "$granted / $total granted",
                style    = MaterialTheme.typography.bodySmall,
                color    = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.weight(1f),
            )
            Text(
                "${(granted * 100f / total.coerceAtLeast(1)).toInt()}%",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.primary,
            )
        }
        LinearProgressIndicator(
            progress = { granted.toFloat() / total.coerceAtLeast(1).toFloat() },
            modifier = Modifier.fillMaxWidth().padding(top = 4.dp),
        )
    }
}

@Composable
private fun RootInstallButton(onInstallRoot: () -> Unit) {
    FilledTonalButton(
        onClick  = onInstallRoot,
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 4.dp),
        colors   = ButtonDefaults.filledTonalButtonColors(
            containerColor = JarvisPalette.GoldDim,
            contentColor   = JarvisPalette.GoldPrimary,
        ),
    ) {
        Icon(Icons.Default.Build, contentDescription = null, modifier = Modifier.size(16.dp))
        Spacer(Modifier.width(8.dp))
        Text("Install Root (Magisk / KernelSU)")
    }
}

@Composable
private fun TierHeader(tier: PermissionTier, entries: List<PermissionEntry>) {
    val grantedCount = entries.count { it.status == PermissionStatus.GRANTED }
    val label = when (tier) {
        PermissionTier.DANGEROUS -> "Runtime Permissions"
        PermissionTier.SPECIAL   -> "Special Permissions"
        PermissionTier.ROOT      -> "Root Access"
    }
    val subtitle = when (tier) {
        PermissionTier.DANGEROUS -> "Granted via system dialog"
        PermissionTier.SPECIAL   -> "Granted via Settings screens"
        PermissionTier.ROOT      -> "Granted by Magisk / KernelSU"
    }
    Row(
        modifier          = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 10.dp),
        verticalAlignment = Alignment.Bottom,
    ) {
        Column(Modifier.weight(1f)) {
            Text(label, style = MaterialTheme.typography.titleSmall)
            Text(subtitle,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Spacer(Modifier.width(8.dp))
        Text(
            "$grantedCount/${entries.size}",
            style = MaterialTheme.typography.labelSmall,
            color = if (grantedCount == entries.size) MaterialTheme.colorScheme.primary
                    else MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

// ── ADB setup banner ──────────────────────────────────────────────────────────

/**
 * Compact banner shown when neither root nor ADB is available.
 * Guides the user through enabling Wireless Debugging and shows a
 * "Connect" button that triggers the one-time key-approval dialog.
 */
@Composable
private fun AdbSetupBanner(
    adbState:  AdbState,
    onConnect: () -> Unit,
    modifier:  Modifier = Modifier,
) {
    val borderColor = when (adbState) {
        is AdbState.Connected  -> Color(0xFF1E7FFF)
        is AdbState.Connecting -> Color(0xFFE0A030)
        is AdbState.Error      -> Color(0xFFCF4A3C)
        else                   -> Color(0xFF2A3A4A)
    }
    Column(
        modifier = modifier
            .fillMaxWidth()
            .border(1.dp, borderColor, MaterialTheme.shapes.small)
            .padding(12.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(
                imageVector        = Icons.Default.Cable,
                contentDescription = null,
                tint               = MaterialTheme.colorScheme.primary,
                modifier           = Modifier.size(18.dp),
            )
            Spacer(Modifier.width(8.dp))
            Text(
                text  = "ADB Auto-Grant",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.primary,
            )
        }
        Spacer(Modifier.height(6.dp))
        Text(
            text  = "Enable once to let JARVIS grant all permissions automatically — no root needed.\n" +
                    "Settings → Developer Options → Wireless Debugging → Enable",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(8.dp))
        when (adbState) {
            is AdbState.Connecting -> {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(8.dp))
                    Text("Connecting…", style = MaterialTheme.typography.bodySmall)
                }
            }
            is AdbState.Error -> {
                Text(
                    text  = "Failed: ${adbState.message}",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                )
                Spacer(Modifier.height(4.dp))
                OutlinedButton(onClick = onConnect, modifier = Modifier.fillMaxWidth()) {
                    Text("Retry Connect")
                }
            }
            else -> {
                OutlinedButton(onClick = onConnect, modifier = Modifier.fillMaxWidth()) {
                    Icon(Icons.Default.Cable, null, Modifier.size(14.dp))
                    Spacer(Modifier.width(6.dp))
                    Text("Connect ADB (localhost:5555)")
                }
                Spacer(Modifier.height(4.dp))
                Text(
                    text  = "Run once from a PC:  adb tcpip 5555",
                    style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}
