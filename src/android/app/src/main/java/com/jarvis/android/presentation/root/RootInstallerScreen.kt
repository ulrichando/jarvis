package com.jarvis.android.presentation.root

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import com.jarvis.android.core.designsystem.JarvisPalette
import com.jarvis.android.core.designsystem.LocalJarvisColors
import kotlin.math.roundToInt

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RootInstallerScreen(
    onBack:    () -> Unit = {},
    viewModel: RootInstallerViewModel = hiltViewModel(),
) {
    val state  by viewModel.state.collectAsState()
    val jarvis  = LocalJarvisColors.current

    Scaffold(
        containerColor = JarvisPalette.ObsidianBlack,
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        "Root Installer",
                        style = MaterialTheme.typography.titleMedium,
                        color = JarvisPalette.GoldPrimary,
                    )
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(
                            Icons.AutoMirrored.Filled.ArrowBack,
                            "Back",
                            tint = JarvisPalette.GoldPrimary,
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor         = JarvisPalette.SurfaceDark,
                    scrolledContainerColor = JarvisPalette.SurfaceDark,
                ),
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .padding(padding)
                .fillMaxSize()
                .background(JarvisPalette.ObsidianBlack)
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
        ) {
            when (val s = state) {
                is InstallerState.CheckingRoot    -> CheckingRootContent()
                is InstallerState.AlreadyRooted   -> AlreadyRootedContent(s.provider, viewModel.deviceInfo)
                is InstallerState.SelectTool      -> SelectToolContent(viewModel.deviceInfo) { viewModel.fetchRelease(it) }
                is InstallerState.FetchingRelease -> FetchingReleaseContent(s.tool)
                is InstallerState.ReadyToDownload -> ReadyToDownloadContent(s,
                    onDownload = { viewModel.download(s.tool, s.downloadUrl) },
                    onBack     = { viewModel.reset() },
                )
                is InstallerState.Downloading     -> DownloadingContent(s)
                is InstallerState.Downloaded      -> DownloadedContent(s,
                    onInstall = { viewModel.installApk(s.apkFile) },
                    onBack    = { viewModel.reset() },
                )
                is InstallerState.Error           -> ErrorContent(s.message) { viewModel.checkRootStatus() }
            }
        }
    }
}

// ── Arc reactor logo ──────────────────────────────────────────────────────────

/**
 * Draws the JARVIS arc reactor (matching the launcher icon) as a Compose Canvas.
 * Used as the hero icon on this screen.
 */
@Composable
private fun ArcReactor(size: Dp = 80.dp, glowing: Boolean = false) {
    val gold    = JarvisPalette.GoldPrimary
    val glow    = JarvisPalette.GoldGlow

    val infiniteTransition = rememberInfiniteTransition(label = "reactor")
    val rotation by infiniteTransition.animateFloat(
        initialValue   = 0f,
        targetValue    = 360f,
        animationSpec  = infiniteRepeatable(tween(6000, easing = LinearEasing), RepeatMode.Restart),
        label          = "rotation",
    )
    val pulse by infiniteTransition.animateFloat(
        initialValue   = 0.6f,
        targetValue    = 1f,
        animationSpec  = infiniteRepeatable(tween(1200), RepeatMode.Reverse),
        label          = "pulse",
    )

    Canvas(modifier = Modifier.size(size)) {
        val w   = this.size.width
        val h   = this.size.height
        val cx  = w / 2f
        val cy  = h / 2f

        // Glow halo
        if (glowing) {
            drawCircle(
                color  = glow.copy(alpha = 0.12f * pulse),
                radius = w * 0.52f,
                center = Offset(cx, cy),
            )
        }

        // Outer ring (4 stroke)
        drawArc(
            color      = gold,
            startAngle = rotation,
            sweepAngle = 300f,
            useCenter  = false,
            topLeft    = Offset(w * 0.07f, h * 0.07f),
            size       = Size(w * 0.86f, h * 0.86f),
            style      = Stroke(width = w * 0.04f, cap = StrokeCap.Round),
        )
        // Outer ring gap filler (dim)
        drawArc(
            color      = gold.copy(alpha = 0.15f),
            startAngle = rotation + 300f,
            sweepAngle = 60f,
            useCenter  = false,
            topLeft    = Offset(w * 0.07f, h * 0.07f),
            size       = Size(w * 0.86f, h * 0.86f),
            style      = Stroke(width = w * 0.04f, cap = StrokeCap.Round),
        )

        // Inner ring (2 stroke, counter-rotate)
        drawArc(
            color      = glow,
            startAngle = -rotation,
            sweepAngle = 240f,
            useCenter  = false,
            topLeft    = Offset(w * 0.22f, h * 0.22f),
            size       = Size(w * 0.56f, h * 0.56f),
            style      = Stroke(width = w * 0.025f, cap = StrokeCap.Round),
        )
        drawArc(
            color      = glow.copy(alpha = 0.15f),
            startAngle = -rotation + 240f,
            sweepAngle = 120f,
            useCenter  = false,
            topLeft    = Offset(w * 0.22f, h * 0.22f),
            size       = Size(w * 0.56f, h * 0.56f),
            style      = Stroke(width = w * 0.025f, cap = StrokeCap.Round),
        )

        // Center dot
        drawCircle(
            color  = gold.copy(alpha = pulse),
            radius = w * 0.06f,
            center = Offset(cx, cy),
        )
    }
}

// ── States ────────────────────────────────────────────────────────────────────

@Composable
private fun CheckingRootContent() {
    Column(
        modifier            = Modifier.fillMaxWidth().padding(vertical = 64.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        ArcReactor(size = 72.dp, glowing = true)
        Spacer(Modifier.height(20.dp))
        Text(
            "Checking root status…",
            style = MaterialTheme.typography.bodyMedium,
            color = JarvisPalette.TextSecondary,
        )
    }
}

@Composable
private fun AlreadyRootedContent(provider: String, deviceInfo: String) {
    Column(
        modifier            = Modifier.fillMaxWidth().padding(vertical = 32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        // Success ring around the reactor
        Box(contentAlignment = Alignment.Center) {
            ArcReactor(size = 88.dp, glowing = true)
            Box(
                modifier = Modifier
                    .size(96.dp)
                    .border(2.dp, JarvisPalette.SuccessGreen.copy(alpha = 0.4f), CircleShape)
            )
        }
        Spacer(Modifier.height(20.dp))
        Text(
            "Device Rooted",
            style = MaterialTheme.typography.headlineMedium,
            color = JarvisPalette.GoldPrimary,
        )
        Spacer(Modifier.height(6.dp))
        Text(
            provider,
            style = MaterialTheme.typography.bodyMedium,
            color = JarvisPalette.SuccessGreen,
        )
        Spacer(Modifier.height(4.dp))
        Text(
            deviceInfo,
            style      = MaterialTheme.typography.labelSmall,
            color      = JarvisPalette.TextSecondary,
            fontFamily = FontFamily.Monospace,
            textAlign  = TextAlign.Center,
        )
        Spacer(Modifier.height(24.dp))
        GoldCard {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(
                    Icons.Default.CheckCircle,
                    contentDescription = null,
                    tint     = JarvisPalette.SuccessGreen,
                    modifier = Modifier.size(18.dp),
                )
                Spacer(Modifier.width(10.dp))
                Text(
                    "JARVIS has full root access via $provider. All system tools are operational.",
                    style = MaterialTheme.typography.bodySmall,
                    color = JarvisPalette.TextPrimary,
                )
            }
        }
    }
}

@Composable
private fun SelectToolContent(deviceInfo: String, onSelect: (RootTool) -> Unit) {
    Column(Modifier.fillMaxWidth()) {
        // Header
        Column(
            modifier            = Modifier.fillMaxWidth().padding(vertical = 24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            ArcReactor(size = 80.dp, glowing = false)
            Spacer(Modifier.height(16.dp))
            Text(
                "Root Access Required",
                style     = MaterialTheme.typography.headlineSmall,
                color     = JarvisPalette.GoldPrimary,
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(6.dp))
            Text(
                "Download and install a root manager to unlock full JARVIS capabilities.",
                style     = MaterialTheme.typography.bodySmall,
                color     = JarvisPalette.TextSecondary,
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(8.dp))
            Text(
                deviceInfo,
                style      = MaterialTheme.typography.labelSmall,
                color      = JarvisPalette.GoldMuted,
                fontFamily = FontFamily.Monospace,
                textAlign  = TextAlign.Center,
            )
        }

        // Warning
        Card(
            modifier = Modifier.fillMaxWidth(),
            shape    = RoundedCornerShape(10.dp),
            colors   = CardDefaults.cardColors(
                containerColor = JarvisPalette.ErrorContainer,
            ),
        ) {
            Row(
                modifier          = Modifier.padding(12.dp),
                verticalAlignment = Alignment.Top,
            ) {
                Icon(
                    Icons.Default.Warning,
                    contentDescription = null,
                    tint     = JarvisPalette.WarningAmber,
                    modifier = Modifier.size(18.dp).padding(top = 1.dp),
                )
                Spacer(Modifier.width(10.dp))
                Text(
                    "Rooting voids warranty and may trip Knox/SafetyNet. Back up all data first.",
                    style = MaterialTheme.typography.bodySmall,
                    color = JarvisPalette.TextPrimary,
                )
            }
        }

        Spacer(Modifier.height(20.dp))

        RootTool.values().forEach { tool ->
            ToolCard(tool, onSelect)
            Spacer(Modifier.height(10.dp))
        }

        Spacer(Modifier.height(20.dp))

        StepsCard(title = "How it works") {
            step("1", "Install Magisk / KernelSU manager APK  ← you are here")
            step("2", "Unlock bootloader:  Settings → Developer Options → OEM Unlock")
            step("3", "Magisk: patch your stock boot.img from within the app")
            step("4", "Flash via fastboot:  fastboot flash boot patched.img")
            step("5", "Reboot — JARVIS will detect root automatically")
        }
    }
}

@Composable
private fun ToolCard(tool: RootTool, onSelect: (RootTool) -> Unit) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .border(1.dp, JarvisPalette.GoldBorder, RoundedCornerShape(12.dp)),
        shape  = RoundedCornerShape(12.dp),
        colors = CardDefaults.cardColors(containerColor = JarvisPalette.SurfaceDark),
    ) {
        Column(Modifier.padding(16.dp)) {
            Text(
                tool.label,
                style = MaterialTheme.typography.titleMedium,
                color = JarvisPalette.GoldPrimary,
            )
            Spacer(Modifier.height(4.dp))
            Text(
                tool.description,
                style = MaterialTheme.typography.bodySmall,
                color = JarvisPalette.TextSecondary,
            )
            Spacer(Modifier.height(14.dp))
            GoldButton(
                label    = "Fetch latest ${tool.label}",
                icon     = Icons.Default.Download,
                onClick  = { onSelect(tool) },
            )
        }
    }
}

@Composable
private fun FetchingReleaseContent(tool: RootTool) {
    Column(
        modifier            = Modifier.fillMaxWidth().padding(vertical = 64.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        ArcReactor(size = 72.dp, glowing = true)
        Spacer(Modifier.height(20.dp))
        Text(
            "Fetching latest ${tool.label} release…",
            style = MaterialTheme.typography.bodyMedium,
            color = JarvisPalette.TextSecondary,
        )
    }
}

@Composable
private fun ReadyToDownloadContent(
    state:      InstallerState.ReadyToDownload,
    onDownload: () -> Unit,
    onBack:     () -> Unit,
) {
    Column(Modifier.fillMaxWidth()) {
        Text(
            "Ready to Download",
            style = MaterialTheme.typography.headlineSmall,
            color = JarvisPalette.GoldPrimary,
        )
        Spacer(Modifier.height(16.dp))

        Card(
            modifier = Modifier
                .fillMaxWidth()
                .border(1.dp, JarvisPalette.GoldBorder, RoundedCornerShape(12.dp)),
            shape  = RoundedCornerShape(12.dp),
            colors = CardDefaults.cardColors(containerColor = JarvisPalette.SurfaceDark),
        ) {
            Column(Modifier.padding(16.dp)) {
                KvRow("Tool",    state.tool.label)
                KvRow("Version", state.version)
                KvRow("Size",    formatBytes(state.sizeBytes))
            }
        }

        Spacer(Modifier.height(16.dp))

        StepsCard(title = "After install") {
            when (state.tool) {
                RootTool.MAGISK -> {
                    step("1", "Install the Magisk APK (next step)")
                    step("2", "Open Magisk → Install → Patch a file → pick boot.img")
                    step("3", "Transfer magisk_patched.img to PC")
                    step("4", "fastboot flash boot magisk_patched.img && fastboot reboot")
                }
                RootTool.KERNELSU -> {
                    step("1", "Install the KernelSU Manager APK (next step)")
                    step("2", "Verify your device has a GKI 2.0 kernel (Android 12+)")
                    step("3", "Download matching KernelSU kernel image from their releases")
                    step("4", "fastboot flash boot kernelsu_boot.img && fastboot reboot")
                }
            }
        }

        Spacer(Modifier.height(24.dp))
        GoldButton(
            label   = "Download ${state.tool.label} ${state.version}",
            icon    = Icons.Default.Download,
            onClick = onDownload,
        )
        Spacer(Modifier.height(8.dp))
        OutlinedButton(
            onClick  = onBack,
            modifier = Modifier.fillMaxWidth(),
            colors   = ButtonDefaults.outlinedButtonColors(contentColor = JarvisPalette.GoldPrimary),
            border   = androidx.compose.foundation.BorderStroke(1.dp, JarvisPalette.GoldBorder),
        ) { Text("Back") }
    }
}

@Composable
private fun DownloadingContent(state: InstallerState.Downloading) {
    Column(
        modifier            = Modifier.fillMaxWidth().padding(vertical = 48.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        ArcReactor(size = 80.dp, glowing = true)
        Spacer(Modifier.height(24.dp))
        Text(
            "Downloading ${state.tool.label}…",
            style = MaterialTheme.typography.bodyMedium,
            color = JarvisPalette.TextPrimary,
        )
        Spacer(Modifier.height(20.dp))
        LinearProgressIndicator(
            progress         = { state.progress },
            modifier         = Modifier.fillMaxWidth(0.85f).height(4.dp),
            color            = JarvisPalette.GoldPrimary,
            trackColor       = JarvisPalette.GoldBorder,
        )
        if (state.progress > 0f) {
            Spacer(Modifier.height(10.dp))
            Text(
                "${(state.progress * 100).roundToInt()}%",
                style = MaterialTheme.typography.labelMedium,
                color = JarvisPalette.GoldPrimary,
            )
        }
    }
}

@Composable
private fun DownloadedContent(
    state:     InstallerState.Downloaded,
    onInstall: () -> Unit,
    onBack:    () -> Unit,
) {
    Column(Modifier.fillMaxWidth()) {
        Column(
            modifier            = Modifier.fillMaxWidth().padding(vertical = 16.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Box(contentAlignment = Alignment.Center) {
                ArcReactor(size = 80.dp, glowing = true)
                Icon(
                    Icons.Default.CheckCircle,
                    contentDescription = null,
                    tint     = JarvisPalette.SuccessGreen,
                    modifier = Modifier
                        .align(Alignment.BottomEnd)
                        .size(24.dp)
                        .background(JarvisPalette.ObsidianBlack, CircleShape),
                )
            }
            Spacer(Modifier.height(14.dp))
            Text(
                "Download Complete",
                style = MaterialTheme.typography.headlineSmall,
                color = JarvisPalette.GoldPrimary,
            )
            Spacer(Modifier.height(4.dp))
            Text(
                state.apkFile.name,
                style      = MaterialTheme.typography.labelSmall,
                color      = JarvisPalette.GoldMuted,
                fontFamily = FontFamily.Monospace,
            )
        }

        StepsCard(title = "Next steps") {
            when (state.tool) {
                RootTool.MAGISK -> {
                    step("1", "Tap Install APK — the Android installer opens")
                    step("2", "Open Magisk, then tap Install → Patch a file")
                    step("3", "Pick your stock boot.img from the firmware package")
                    step("4", "Flash magisk_patched.img via fastboot and reboot")
                    step("5", "Return here to verify root access")
                }
                RootTool.KERNELSU -> {
                    step("1", "Tap Install APK — the Android installer opens")
                    step("2", "Open KernelSU Manager and check device compatibility")
                    step("3", "Download the matching kernel image from KernelSU releases")
                    step("4", "fastboot flash boot kernelsu_boot.img && reboot")
                    step("5", "Return here to verify root access")
                }
            }
        }

        Spacer(Modifier.height(24.dp))
        GoldButton(label = "Install ${state.tool.label} APK", onClick = onInstall)
        Spacer(Modifier.height(8.dp))
        OutlinedButton(
            onClick  = onBack,
            modifier = Modifier.fillMaxWidth(),
            colors   = ButtonDefaults.outlinedButtonColors(contentColor = JarvisPalette.GoldPrimary),
            border   = androidx.compose.foundation.BorderStroke(1.dp, JarvisPalette.GoldBorder),
        ) { Text("Choose different tool") }
    }
}

@Composable
private fun ErrorContent(message: String, onRetry: () -> Unit) {
    Column(
        modifier            = Modifier.fillMaxWidth().padding(vertical = 40.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Icon(
            Icons.Default.Warning,
            contentDescription = null,
            tint     = JarvisPalette.ErrorRed,
            modifier = Modifier.size(48.dp),
        )
        Spacer(Modifier.height(16.dp))
        Text(
            message,
            style     = MaterialTheme.typography.bodyMedium,
            color     = JarvisPalette.ErrorRed,
            textAlign = TextAlign.Center,
        )
        Spacer(Modifier.height(24.dp))
        Button(
            onClick  = onRetry,
            colors   = ButtonDefaults.buttonColors(
                containerColor = JarvisPalette.ErrorRed,
                contentColor   = JarvisPalette.TextPrimary,
            ),
        ) { Text("Retry") }
    }
}

// ── Reusable components ───────────────────────────────────────────────────────

@Composable
private fun GoldButton(
    label:   String,
    icon:    androidx.compose.ui.graphics.vector.ImageVector? = null,
    onClick: () -> Unit,
) {
    Button(
        onClick  = onClick,
        modifier = Modifier.fillMaxWidth(),
        colors   = ButtonDefaults.buttonColors(
            containerColor = JarvisPalette.GoldPrimary,
            contentColor   = JarvisPalette.TextOnGold,
        ),
        shape = RoundedCornerShape(10.dp),
    ) {
        if (icon != null) {
            Icon(icon, contentDescription = null, modifier = Modifier.size(16.dp))
            Spacer(Modifier.width(8.dp))
        }
        Text(label, fontFamily = FontFamily.Default)
    }
}

@Composable
private fun GoldCard(content: @Composable () -> Unit) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .border(1.dp, JarvisPalette.GoldBorder, RoundedCornerShape(10.dp)),
        shape  = RoundedCornerShape(10.dp),
        colors = CardDefaults.cardColors(containerColor = JarvisPalette.GoldDim),
    ) {
        Box(Modifier.padding(14.dp)) { content() }
    }
}

@Composable
private fun StepsCard(title: String, content: @Composable () -> Unit) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .border(1.dp, JarvisPalette.GoldBorder.copy(alpha = 0.5f), RoundedCornerShape(10.dp)),
        shape  = RoundedCornerShape(10.dp),
        colors = CardDefaults.cardColors(containerColor = JarvisPalette.SurfaceElevated),
    ) {
        Column(Modifier.padding(14.dp)) {
            Text(
                title,
                style     = MaterialTheme.typography.labelMedium,
                color     = JarvisPalette.GoldPrimary,
                fontSize  = 11.sp,
                letterSpacing = 0.8.sp,
            )
            Spacer(Modifier.height(10.dp))
            content()
        }
    }
}

@Composable
private fun step(num: String, text: String) {
    Row(modifier = Modifier.padding(vertical = 3.dp), verticalAlignment = Alignment.Top) {
        Text(
            "$num.",
            style    = MaterialTheme.typography.labelSmall,
            color    = JarvisPalette.GoldPrimary,
            modifier = Modifier.width(18.dp),
        )
        Text(
            text,
            style = MaterialTheme.typography.bodySmall,
            color = JarvisPalette.TextPrimary,
        )
    }
}

@Composable
private fun KvRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 3.dp)) {
        Text(
            label,
            style    = MaterialTheme.typography.bodySmall,
            color    = JarvisPalette.TextSecondary,
            modifier = Modifier.weight(0.35f),
        )
        Text(
            value,
            style  = MaterialTheme.typography.bodySmall,
            color  = JarvisPalette.TextPrimary,
            modifier = Modifier.weight(0.65f),
        )
    }
}

private fun formatBytes(bytes: Long): String = when {
    bytes <= 0        -> "Unknown"
    bytes < 1_024     -> "$bytes B"
    bytes < 1_048_576 -> "${bytes / 1_024} KB"
    else              -> "%.1f MB".format(bytes / 1_048_576.0)
}
