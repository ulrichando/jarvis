package com.jarvis.android.presentation.cyber

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
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
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.BugReport
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Dns
import androidx.compose.material.icons.filled.ErrorOutline
import androidx.compose.material.icons.filled.Language
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.TabRowDefaults
import androidx.compose.material3.TabRowDefaults.tabIndicatorOffset
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import com.jarvis.android.core.designsystem.JarvisPalette
import com.jarvis.android.domain.model.CyberProcess
import com.jarvis.android.domain.model.LogLevel
import com.jarvis.android.domain.model.NetworkConnection
import com.jarvis.android.domain.model.PortResult
import com.jarvis.android.domain.model.ScanState

// ── Palette ───────────────────────────────────────────────────────────────────

private val Obsidian      = JarvisPalette.ObsidianBlack
private val Gold          = JarvisPalette.GoldPrimary
private val Surface       = JarvisPalette.SurfaceDark
private val Surface2      = JarvisPalette.SurfaceElevated
private val TextPrimary   = JarvisPalette.TextPrimary
private val TextSecondary = JarvisPalette.TextSecondary
private val Danger        = JarvisPalette.ErrorRed
private val Success       = JarvisPalette.SuccessGreen
private val Warning       = JarvisPalette.WarningAmber

// ── Root ──────────────────────────────────────────────────────────────────────

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CyberScreen(
    onBack: () -> Unit = {},
    vm: CyberViewModel = hiltViewModel(),
) {
    val ui by vm.ui.collectAsState()
    var selectedTab by rememberSaveable { mutableIntStateOf(0) }

    val tabs = listOf("Port Scan", "HTTP", "Processes", "Network", "Logcat")

    Scaffold(
        containerColor = Obsidian,
        topBar = {
            Column {
                TopAppBar(
                    title = {
                        Text(
                            "Cyber Suite",
                            color      = Danger,
                            fontWeight = FontWeight.Bold,
                            fontSize   = 18.sp,
                        )
                    },
                    navigationIcon = {
                        IconButton(onClick = onBack) {
                            Icon(Icons.Default.ArrowBack, null, tint = Gold)
                        }
                    },
                    colors = TopAppBarDefaults.topAppBarColors(containerColor = Obsidian),
                )
                TabRow(
                    selectedTabIndex = selectedTab,
                    containerColor   = Obsidian,
                    indicator = { tabPositions ->
                        TabRowDefaults.Indicator(
                            modifier = Modifier.tabIndicatorOffset(tabPositions[selectedTab]),
                            color    = Danger,
                        )
                    },
                ) {
                    tabs.forEachIndexed { i, label ->
                        Tab(
                            selected = selectedTab == i,
                            onClick  = { selectedTab = i },
                            text = {
                                Text(
                                    label,
                                    color    = if (selectedTab == i) Danger else TextSecondary,
                                    fontSize = 12.sp,
                                )
                            },
                        )
                    }
                }
            }
        },
    ) { padding ->
        Box(Modifier.padding(padding).fillMaxSize()) {
            when (selectedTab) {
                0 -> PortScanTab(ui, vm)
                1 -> HttpInspectTab(ui, vm)
                2 -> ProcessTab(ui, vm)
                3 -> NetworkTab(ui, vm)
                4 -> LogcatTab(ui, vm)
            }
        }
    }
}

// ── Port Scan ─────────────────────────────────────────────────────────────────

@Composable
private fun PortScanTab(ui: CyberUiState, vm: CyberViewModel) {
    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(
                value         = ui.scanTarget,
                onValueChange = vm::setScanTarget,
                label         = { Text("Target (IP or hostname)", color = TextSecondary) },
                singleLine    = true,
                colors        = cyberFieldColors(),
                modifier      = Modifier.weight(1f),
            )
            Button(
                onClick  = vm::startPortScan,
                enabled  = ui.scanTarget.isNotBlank() && ui.scanState != ScanState.RUNNING,
                colors   = ButtonDefaults.buttonColors(containerColor = Danger, disabledContainerColor = Surface2),
            ) {
                Icon(Icons.Default.PlayArrow, null, modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(4.dp))
                Text("Scan", fontSize = 13.sp)
            }
        }

        if (ui.scanState == ScanState.RUNNING) {
            Column {
                Text("Scanning… ${(ui.scanProgress * 100).toInt()}%", color = TextSecondary, fontSize = 12.sp)
                Spacer(Modifier.height(4.dp))
                LinearProgressIndicator(
                    progress = { ui.scanProgress },
                    color    = Danger,
                    trackColor = Surface2,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        }

        ui.portScanResult?.let { result ->
            val duration = "%.1fs".format(result.durationMs / 1000.0)
            Text(
                "${result.openPorts.size} open ports on ${result.target} · $duration · ${result.totalScanned} scanned",
                color    = TextSecondary,
                fontSize = 12.sp,
            )
            if (result.openPorts.isEmpty()) {
                Text("No open ports found.", color = TextSecondary, fontSize = 13.sp)
            } else {
                result.openPorts.forEach { PortResultRow(it) }
            }
        }
    }
}

@Composable
private fun PortResultRow(port: PortResult) {
    Card(
        colors   = CardDefaults.cardColors(containerColor = Surface),
        shape    = RoundedCornerShape(8.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "${port.port}",
                    color      = Success,
                    fontWeight = FontWeight.Bold,
                    fontSize   = 14.sp,
                    fontFamily = FontFamily.Monospace,
                    modifier   = Modifier.width(52.dp),
                )
                port.service?.let {
                    Text(it, color = Gold, fontSize = 13.sp, modifier = Modifier.weight(1f))
                }
                Text("${port.latencyMs}ms", color = TextSecondary, fontSize = 11.sp)
            }
            port.banner?.let { banner ->
                Spacer(Modifier.height(4.dp))
                Text(
                    banner,
                    color      = Color(0xFF90EE90),
                    fontSize   = 10.sp,
                    fontFamily = FontFamily.Monospace,
                    maxLines   = 3,
                    overflow   = TextOverflow.Ellipsis,
                )
            }
        }
    }
}

// ── HTTP Inspect ──────────────────────────────────────────────────────────────

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun HttpInspectTab(ui: CyberUiState, vm: CyberViewModel) {
    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(
                value         = ui.httpTarget,
                onValueChange = vm::setHttpTarget,
                label         = { Text("URL or hostname", color = TextSecondary) },
                singleLine    = true,
                colors        = cyberFieldColors(),
                modifier      = Modifier.weight(1f),
            )
            Button(
                onClick  = vm::startHttpInspect,
                enabled  = ui.httpTarget.isNotBlank() && ui.httpState != ScanState.RUNNING,
                colors   = ButtonDefaults.buttonColors(containerColor = Danger, disabledContainerColor = Surface2),
            ) {
                if (ui.httpState == ScanState.RUNNING)
                    CircularProgressIndicator(color = Color.White, strokeWidth = 2.dp, modifier = Modifier.size(18.dp))
                else
                    Icon(Icons.Default.Language, null, modifier = Modifier.size(18.dp))
            }
        }

        ui.httpResult?.let { result ->
            // Status
            val statusColor = when {
                result.statusCode in 200..299 -> Success
                result.statusCode in 300..399 -> Warning
                result.statusCode >= 400      -> Danger
                else -> TextSecondary
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("HTTP ${result.statusCode}", color = statusColor, fontWeight = FontWeight.Bold, fontSize = 16.sp)
                Spacer(Modifier.width(8.dp))
                Text("${result.durationMs}ms", color = TextSecondary, fontSize = 12.sp)
            }

            // Security header grade
            val sec = result.securityHeaders
            val gradeColor = when (sec.grade) {
                "A+" -> Success
                "A"  -> Success
                "B"  -> Warning
                else -> Danger
            }
            Card(colors = CardDefaults.cardColors(containerColor = Surface), shape = RoundedCornerShape(10.dp)) {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("Security Headers", color = Gold, fontWeight = FontWeight.SemiBold, fontSize = 13.sp)
                        Spacer(Modifier.weight(1f))
                        Text(sec.grade, color = gradeColor, fontWeight = FontWeight.Bold, fontSize = 18.sp)
                        Text(" (${sec.score}/6)", color = TextSecondary, fontSize = 12.sp)
                    }
                    SecurityHeaderRow("HSTS",               sec.hsts)
                    SecurityHeaderRow("CSP",                sec.csp)
                    SecurityHeaderRow("X-Frame-Options",    sec.xFrameOptions)
                    SecurityHeaderRow("X-Content-Type",     sec.xContentTypeNoSniff)
                    SecurityHeaderRow("Referrer-Policy",    sec.referrerPolicy)
                    SecurityHeaderRow("Permissions-Policy", sec.permissionsPolicy)
                }
            }

            // TLS
            result.tlsInfo?.let { tls ->
                Card(colors = CardDefaults.cardColors(containerColor = Surface), shape = RoundedCornerShape(10.dp)) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Text("TLS", color = Gold, fontWeight = FontWeight.SemiBold, fontSize = 13.sp)
                        InfoRow("Protocol", tls.protocol)
                        InfoRow("Cipher",   tls.cipher)
                        InfoRow("Valid until", tls.validUntil)
                        InfoRow("Issuer",   tls.issuer)
                    }
                }
            }

            // Redirect chain
            if (result.redirectChain.isNotEmpty()) {
                Column {
                    Text("Redirect chain", color = Gold, fontSize = 13.sp)
                    result.redirectChain.forEachIndexed { i, url ->
                        Text("$i → $url", color = TextSecondary, fontSize = 11.sp, fontFamily = FontFamily.Monospace)
                    }
                }
            }

            // Raw response headers
            if (result.headers.isNotEmpty()) {
                Text("Response Headers", color = Gold, fontSize = 13.sp)
                result.headers.entries.sortedBy { it.key }.forEach { (k, v) ->
                    Row(Modifier.fillMaxWidth()) {
                        Text("$k:", color = TextSecondary, fontSize = 10.sp, fontFamily = FontFamily.Monospace, modifier = Modifier.width(180.dp))
                        Text(v, color = TextPrimary, fontSize = 10.sp, fontFamily = FontFamily.Monospace, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    }
                }
            }
        }
    }
}

@Composable
private fun SecurityHeaderRow(label: String, present: Boolean) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Icon(
            if (present) Icons.Default.CheckCircle else Icons.Default.ErrorOutline,
            null,
            tint     = if (present) Success else Danger,
            modifier = Modifier.size(14.dp),
        )
        Text(label, color = if (present) TextPrimary else TextSecondary, fontSize = 12.sp)
    }
}

@Composable
private fun InfoRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth()) {
        Text("$label:", color = TextSecondary, fontSize = 11.sp, modifier = Modifier.width(100.dp))
        Text(value, color = TextPrimary, fontSize = 11.sp, fontFamily = FontFamily.Monospace, maxLines = 2, overflow = TextOverflow.Ellipsis)
    }
}

// ── Processes ─────────────────────────────────────────────────────────────────

@Composable
private fun ProcessTab(ui: CyberUiState, vm: CyberViewModel) {
    Column(Modifier.fillMaxSize()) {
        // Toolbar
        Row(
            Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment     = Alignment.CenterVertically,
        ) {
            OutlinedTextField(
                value         = ui.processFilter,
                onValueChange = vm::setProcessFilter,
                label         = { Text("Filter", color = TextSecondary) },
                singleLine    = true,
                colors        = cyberFieldColors(),
                modifier      = Modifier.weight(1f).height(52.dp),
            )
            IconButton(
                onClick  = vm::refreshProcesses,
                enabled  = ui.processState != ScanState.RUNNING,
            ) {
                if (ui.processState == ScanState.RUNNING)
                    CircularProgressIndicator(color = Danger, strokeWidth = 2.dp, modifier = Modifier.size(22.dp))
                else
                    Icon(Icons.Default.Refresh, null, tint = Danger)
            }
        }

        // Summary
        ui.processSnap?.let { snap ->
            Text(
                "${snap.processes.size} processes · ${snap.suspicious.size} suspicious",
                color    = if (snap.suspicious.isNotEmpty()) Danger else TextSecondary,
                fontSize = 11.sp,
                modifier = Modifier.padding(horizontal = 14.dp),
            )
        }

        LazyColumn(Modifier.fillMaxSize().padding(horizontal = 12.dp, vertical = 4.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            items(ui.filteredProcesses, key = { it.pid }) { proc ->
                ProcessCard(proc)
            }
            item { Spacer(Modifier.height(16.dp)) }
        }
    }
}

@Composable
private fun ProcessCard(proc: CyberProcess) {
    val borderColor = if (proc.suspicious) Danger else Color.Transparent
    Card(
        modifier = Modifier.fillMaxWidth().border(
            width = if (proc.suspicious) 1.dp else 0.dp,
            color = borderColor,
            shape = RoundedCornerShape(8.dp),
        ),
        colors   = CardDefaults.cardColors(containerColor = if (proc.suspicious) Color(0xFF2A0A0A) else Surface),
        shape    = RoundedCornerShape(8.dp),
    ) {
        Column(Modifier.padding(10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                if (proc.suspicious) Icon(Icons.Default.Warning, null, tint = Danger, modifier = Modifier.size(14.dp).padding(end = 4.dp))
                Text(proc.name, color = if (proc.suspicious) Danger else TextPrimary, fontWeight = FontWeight.SemiBold, fontSize = 13.sp, modifier = Modifier.weight(1f))
                Text("PID ${proc.pid}", color = TextSecondary, fontSize = 10.sp)
            }
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Text("UID ${proc.user}", color = TextSecondary, fontSize = 10.sp)
                Text("RSS ${proc.rssKb / 1024}MB", color = TextSecondary, fontSize = 10.sp)
                Text(proc.state, color = TextSecondary, fontSize = 10.sp)
            }
            if (proc.cmdline.isNotBlank()) {
                Text(proc.cmdline, color = Color(0xFF6A9A6A), fontSize = 9.sp, fontFamily = FontFamily.Monospace, maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
            proc.reason?.let { Text(it, color = Danger, fontSize = 10.sp) }
        }
    }
}

// ── Network Connections ───────────────────────────────────────────────────────

@Composable
private fun NetworkTab(ui: CyberUiState, vm: CyberViewModel) {
    Column(Modifier.fillMaxSize()) {
        Row(
            Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment     = Alignment.CenterVertically,
        ) {
            FilterChip(
                selected = ui.showSuspiciousOnly,
                onClick  = vm::toggleSuspiciousOnly,
                label    = { Text("Suspicious only", fontSize = 12.sp) },
                leadingIcon = { Icon(Icons.Default.Shield, null, modifier = Modifier.size(14.dp)) },
                colors   = FilterChipDefaults.filterChipColors(
                    selectedContainerColor = Danger.copy(alpha = 0.2f),
                    selectedLabelColor     = Danger,
                    containerColor         = Surface,
                    labelColor             = TextSecondary,
                ),
            )
            Spacer(Modifier.weight(1f))
            IconButton(onClick = vm::refreshNetwork, enabled = ui.networkState != ScanState.RUNNING) {
                if (ui.networkState == ScanState.RUNNING)
                    CircularProgressIndicator(color = Danger, strokeWidth = 2.dp, modifier = Modifier.size(22.dp))
                else
                    Icon(Icons.Default.Refresh, null, tint = Danger)
            }
        }

        ui.networkSnap?.let { snap ->
            Text(
                "${snap.connections.size} connections · ${snap.suspicious.size} suspicious",
                color    = if (snap.suspicious.isNotEmpty()) Danger else TextSecondary,
                fontSize = 11.sp,
                modifier = Modifier.padding(horizontal = 14.dp),
            )
        }

        LazyColumn(Modifier.fillMaxSize().padding(horizontal = 12.dp, vertical = 4.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            items(ui.filteredConnections) { conn ->
                NetworkConnectionCard(conn)
            }
            item { Spacer(Modifier.height(16.dp)) }
        }
    }
}

@Composable
private fun NetworkConnectionCard(conn: NetworkConnection) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors   = CardDefaults.cardColors(containerColor = if (conn.suspicious) Color(0xFF2A0A0A) else Surface),
        shape    = RoundedCornerShape(8.dp),
    ) {
        Row(Modifier.padding(10.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text(conn.protocol.uppercase(), color = Gold, fontSize = 10.sp, fontWeight = FontWeight.Bold)
                    Text(conn.state, color = stateColor(conn.state), fontSize = 10.sp)
                    if (conn.suspicious) Icon(Icons.Default.Warning, null, tint = Danger, modifier = Modifier.size(12.dp))
                }
                Text(
                    "${conn.localAddr}:${conn.localPort}",
                    color = TextPrimary, fontSize = 11.sp, fontFamily = FontFamily.Monospace,
                )
                if (conn.remotePort > 0) {
                    Text(
                        "→ ${conn.remoteAddr}:${conn.remotePort}",
                        color = TextSecondary, fontSize = 11.sp, fontFamily = FontFamily.Monospace,
                    )
                }
            }
        }
    }
}

private fun stateColor(state: String) = when (state) {
    "LISTEN"      -> Warning
    "ESTABLISHED" -> Success
    "TIME_WAIT"   -> Color(0xFF5B8EDE)
    "CLOSE_WAIT"  -> Color(0xFFBB86FC)
    else          -> TextSecondary
}

// ── Logcat ────────────────────────────────────────────────────────────────────

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun LogcatTab(ui: CyberUiState, vm: CyberViewModel) {
    Column(Modifier.fillMaxSize()) {
        // Toolbar
        Column(Modifier.padding(horizontal = 12.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    value         = ui.logFilter,
                    onValueChange = vm::setLogFilter,
                    label         = { Text("Filter tag/message", color = TextSecondary) },
                    singleLine    = true,
                    colors        = cyberFieldColors(),
                    modifier      = Modifier.weight(1f).height(52.dp),
                )
                Button(
                    onClick  = if (ui.logState == ScanState.RUNNING) vm::stopLogWatch else vm::startLogWatch,
                    colors   = ButtonDefaults.buttonColors(
                        containerColor = if (ui.logState == ScanState.RUNNING) TextSecondary else Danger,
                    ),
                ) {
                    Icon(
                        if (ui.logState == ScanState.RUNNING) Icons.Default.Stop else Icons.Default.PlayArrow,
                        null, modifier = Modifier.size(18.dp),
                    )
                }
            }
            // Level filter
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                listOf(LogLevel.VERBOSE, LogLevel.DEBUG, LogLevel.INFO, LogLevel.WARN, LogLevel.ERROR).forEach { lvl ->
                    FilterChip(
                        selected = ui.logMinLevel == lvl,
                        onClick  = { vm.setLogMinLevel(lvl) },
                        label    = { Text(lvl.name.take(1), fontSize = 11.sp) },
                        colors   = FilterChipDefaults.filterChipColors(
                            selectedContainerColor = logLevelColor(lvl).copy(alpha = 0.3f),
                            selectedLabelColor     = logLevelColor(lvl),
                            containerColor         = Surface,
                            labelColor             = TextSecondary,
                        ),
                    )
                }
            }
        }

        Text(
            "${ui.filteredLogs.size} entries",
            color    = TextSecondary,
            fontSize = 10.sp,
            modifier = Modifier.padding(horizontal = 14.dp),
        )

        LazyColumn(Modifier.fillMaxSize().padding(horizontal = 8.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            items(ui.filteredLogs) { entry ->
                LogEntryRow(entry)
            }
            item { Spacer(Modifier.height(16.dp)) }
        }
    }
}

@Composable
private fun LogEntryRow(entry: com.jarvis.android.domain.model.LogEntry) {
    val isSecurityRelevant = com.jarvis.android.system.cyber.LogWatcher.SECURITY_PATTERNS
        .any { entry.message.contains(it, ignoreCase = true) || entry.tag.contains(it, ignoreCase = true) }

    Row(
        Modifier
            .fillMaxWidth()
            .background(if (isSecurityRelevant) Color(0xFF1A0A0A) else Color.Transparent)
            .padding(vertical = 1.dp, horizontal = 4.dp),
    ) {
        Text(
            entry.level.name.take(1),
            color      = logLevelColor(entry.level),
            fontSize   = 10.sp,
            fontFamily = FontFamily.Monospace,
            modifier   = Modifier.width(12.dp),
        )
        Spacer(Modifier.width(4.dp))
        Text(
            entry.tag,
            color      = Gold.copy(alpha = 0.8f),
            fontSize   = 10.sp,
            fontFamily = FontFamily.Monospace,
            modifier   = Modifier.width(100.dp),
            maxLines   = 1,
            overflow   = TextOverflow.Ellipsis,
        )
        Spacer(Modifier.width(6.dp))
        Text(
            entry.message,
            color      = if (isSecurityRelevant) Danger else TextSecondary,
            fontSize   = 10.sp,
            fontFamily = FontFamily.Monospace,
            maxLines   = 2,
            overflow   = TextOverflow.Ellipsis,
            modifier   = Modifier.weight(1f),
        )
    }
}

private fun logLevelColor(level: LogLevel) = when (level) {
    LogLevel.VERBOSE -> TextSecondary
    LogLevel.DEBUG   -> Color(0xFF5B8EDE)
    LogLevel.INFO    -> Success
    LogLevel.WARN    -> Warning
    LogLevel.ERROR   -> Danger
    LogLevel.FATAL   -> Color(0xFFFF1744)
    LogLevel.UNKNOWN -> TextSecondary
}

// ── Style helpers ─────────────────────────────────────────────────────────────

@Composable
private fun cyberFieldColors() = OutlinedTextFieldDefaults.colors(
    focusedTextColor      = TextPrimary,
    unfocusedTextColor    = TextPrimary,
    focusedBorderColor    = Danger.copy(alpha = 0.7f),
    unfocusedBorderColor  = Color(0xFF333333),
    cursorColor           = Danger,
    focusedContainerColor    = Surface,
    unfocusedContainerColor  = Surface,
    focusedLabelColor     = Danger,
)
