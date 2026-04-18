package com.jarvis.android.domain.model

// ── Shared ────────────────────────────────────────────────────────────────────

enum class ScanState { IDLE, RUNNING, DONE, ERROR }

// ── Red Team ──────────────────────────────────────────────────────────────────

data class PortResult(
    val port:     Int,
    val open:     Boolean,
    val banner:   String? = null,
    val service:  String? = null,
    val latencyMs: Long   = 0,
)

data class PortScanResult(
    val target:    String,
    val openPorts: List<PortResult>,
    val totalScanned: Int,
    val durationMs:   Long,
    val error:     String? = null,
)

data class HttpInspectResult(
    val url:         String,
    val statusCode:  Int,
    val headers:     Map<String, String>,
    val securityHeaders: SecurityHeaders,
    val redirectChain: List<String>,
    val tlsInfo:     TlsInfo?,
    val durationMs:  Long,
    val error:       String? = null,
)

data class SecurityHeaders(
    val hsts:              Boolean,
    val csp:               Boolean,
    val xFrameOptions:     Boolean,
    val xContentTypeNoSniff: Boolean,
    val referrerPolicy:    Boolean,
    val permissionsPolicy: Boolean,
) {
    val score: Int
        get() = listOf(hsts, csp, xFrameOptions, xContentTypeNoSniff, referrerPolicy, permissionsPolicy)
            .count { it }
    val grade: String
        get() = when (score) {
            6    -> "A+"
            5    -> "A"
            4    -> "B"
            3    -> "C"
            2    -> "D"
            else -> "F"
        }
}

data class TlsInfo(
    val protocol:  String,
    val cipher:    String,
    val issuer:    String,
    val validUntil: String,
)

// ── Blue Team ─────────────────────────────────────────────────────────────────

data class CyberProcess(
    val pid:       Int,
    val name:      String,
    val user:      String,
    val state:     String,
    val rssKb:     Long,
    val cpuPct:    Float,
    val cmdline:   String,
    val suspicious: Boolean = false,
    val reason:    String?  = null,
)

data class NetworkConnection(
    val protocol:   String,   // tcp, tcp6, udp
    val localAddr:  String,
    val localPort:  Int,
    val remoteAddr: String,
    val remotePort: Int,
    val state:      String,
    val pid:        Int?,
    val suspicious: Boolean = false,
)

data class LogEntry(
    val timestampMs: Long,
    val level:       LogLevel,
    val tag:         String,
    val message:     String,
    val raw:         String,
)

enum class LogLevel { VERBOSE, DEBUG, INFO, WARN, ERROR, FATAL, UNKNOWN }

data class ProcessSnapshot(
    val processes:  List<CyberProcess>,
    val suspicious: List<CyberProcess>,
    val takenAtMs:  Long = System.currentTimeMillis(),
)

data class NetworkSnapshot(
    val connections: List<NetworkConnection>,
    val suspicious:  List<NetworkConnection>,
    val takenAtMs:   Long = System.currentTimeMillis(),
)
