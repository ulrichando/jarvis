package com.jarvis.android.system.cyber

import android.util.Log
import com.jarvis.android.domain.model.PortResult
import com.jarvis.android.domain.model.PortScanResult
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.withContext
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.InetSocketAddress
import java.net.Socket
import javax.inject.Inject
import javax.inject.Singleton

/**
 * TCP connect port scanner.
 *
 * Scans a target host's ports using parallel coroutines. Each port attempt
 * uses a short [CONNECT_TIMEOUT_MS] to avoid blocking the main scan.
 * Banner grabbing is opportunistic — if the service sends data within
 * [BANNER_TIMEOUT_MS] of connecting, it is captured.
 */
@Singleton
class PortScanner @Inject constructor() {

    /**
     * Scan [target] for the given [ports].
     *
     * Runs [PARALLELISM] concurrent socket probes. Progress is emitted via [onProgress].
     *
     * @param target  hostname or IP address
     * @param ports   list of ports to scan (default: [COMMON_PORTS])
     * @param grabBanners whether to attempt banner grabbing on open ports
     */
    suspend fun scan(
        target:       String,
        ports:        List<Int> = COMMON_PORTS,
        grabBanners:  Boolean   = true,
        onProgress:   (scanned: Int, total: Int) -> Unit = { _, _ -> },
    ): PortScanResult = withContext(Dispatchers.IO) {
        val start = System.currentTimeMillis()
        var scanned = 0

        try {
            val chunks = ports.chunked(PARALLELISM)
            val results = mutableListOf<PortResult>()

            for (chunk in chunks) {
                val deferred = chunk.map { port ->
                    async {
                        probePort(target, port, grabBanners).also {
                            synchronized(results) {
                                scanned++
                                onProgress(scanned, ports.size)
                            }
                        }
                    }
                }
                results.addAll(deferred.awaitAll())
            }

            val open = results.filter { it.open }.sortedBy { it.port }
            PortScanResult(
                target       = target,
                openPorts    = open,
                totalScanned = ports.size,
                durationMs   = System.currentTimeMillis() - start,
            )
        } catch (e: Exception) {
            Log.e(TAG, "Scan failed for $target", e)
            PortScanResult(
                target       = target,
                openPorts    = emptyList(),
                totalScanned = scanned,
                durationMs   = System.currentTimeMillis() - start,
                error        = e.message,
            )
        }
    }

    private fun probePort(host: String, port: Int, grabBanner: Boolean): PortResult {
        val tStart = System.currentTimeMillis()
        return try {
            Socket().use { socket ->
                socket.soTimeout = BANNER_TIMEOUT_MS
                socket.connect(InetSocketAddress(host, port), CONNECT_TIMEOUT_MS)
                val latency = System.currentTimeMillis() - tStart

                val banner = if (grabBanner) {
                    tryGrabBanner(socket)
                } else null

                PortResult(
                    port      = port,
                    open      = true,
                    banner    = banner,
                    service   = WELL_KNOWN_SERVICES[port],
                    latencyMs = latency,
                )
            }
        } catch (_: Exception) {
            PortResult(port = port, open = false)
        }
    }

    private fun tryGrabBanner(socket: Socket): String? {
        return try {
            socket.soTimeout = BANNER_TIMEOUT_MS
            val reader = BufferedReader(InputStreamReader(socket.getInputStream()))
            val sb = StringBuilder()
            var line: String?
            var lines = 0
            while (reader.readLine().also { line = it } != null && lines < 3) {
                sb.append(line).append('\n')
                lines++
            }
            sb.toString().trim().take(256).ifBlank { null }
        } catch (_: Exception) {
            null
        }
    }

    companion object {
        private const val TAG                 = "PortScanner"
        private const val CONNECT_TIMEOUT_MS  = 800
        private const val BANNER_TIMEOUT_MS   = 1000
        private const val PARALLELISM         = 50

        /** ~100 most relevant ports for Android/IoT/web environments. */
        val COMMON_PORTS = listOf(
            21, 22, 23, 25, 53, 80, 110, 111, 119, 135, 139, 143, 161, 194,
            443, 445, 465, 514, 515, 587, 631, 993, 995,
            1080, 1194, 1433, 1521, 1883, 2049, 2375, 2376, 3000, 3306, 3389,
            4369, 5000, 5432, 5672, 5900, 5984, 6379, 6443, 7000, 7001, 8080,
            8443, 8888, 9000, 9090, 9200, 9300, 11211, 15672, 27017, 50070,
        )

        val WELL_KNOWN_SERVICES = mapOf(
            21    to "FTP",       22  to "SSH",        23  to "Telnet",
            25    to "SMTP",      53  to "DNS",         80  to "HTTP",
            110   to "POP3",      111 to "RPC",         135 to "MSRPC",
            139   to "NetBIOS",   143 to "IMAP",        161 to "SNMP",
            443   to "HTTPS",     445 to "SMB",         465 to "SMTPS",
            514   to "Syslog",    587 to "SMTP",        631 to "IPP",
            993   to "IMAPS",     995 to "POP3S",       1080 to "SOCKS",
            1433  to "MSSQL",     1521 to "Oracle",     1883 to "MQTT",
            2375  to "Docker",    2376 to "Docker TLS", 3000 to "Dev HTTP",
            3306  to "MySQL",     3389 to "RDP",        5432 to "PostgreSQL",
            5672  to "AMQP",      5900 to "VNC",        5984 to "CouchDB",
            6379  to "Redis",     6443 to "K8s API",    7001 to "WebLogic",
            8080  to "HTTP Alt",  8443 to "HTTPS Alt",  8888 to "Jupyter",
            9000  to "SonarQube", 9090 to "Prometheus", 9200 to "Elasticsearch",
            11211 to "Memcached", 27017 to "MongoDB",
        )
    }
}
