package com.jarvis.android.system.cyber

import android.util.Log
import com.jarvis.android.domain.model.NetworkConnection
import com.jarvis.android.domain.model.NetworkSnapshot
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Reads active network connections from `/proc/net/tcp`, `tcp6`, `udp`, and `udp6`.
 *
 * Does NOT require root — these files are world-readable on Android.
 * IP addresses are stored in `/proc/net/tcp` as little-endian hex; we convert
 * them to dotted-decimal notation.
 *
 * ## Suspicious heuristics
 *
 * - Listening on a port that is not in the expected set for an Android device
 * - Connection to a known-bad port (e.g. 4444, 1337, 31337)
 * - Non-loopback listener on an unusual port
 */
@Singleton
class NetworkMonitor @Inject constructor() {

    suspend fun snapshot(): NetworkSnapshot = withContext(Dispatchers.IO) {
        val connections = mutableListOf<NetworkConnection>()

        listOf("tcp" to "tcp", "tcp6" to "tcp6", "udp" to "udp", "udp6" to "udp6")
            .forEach { (proto, file) ->
                connections.addAll(parseNetFile("/proc/net/$file", proto))
            }

        val flagged = connections.map { flag(it) }
        NetworkSnapshot(
            connections = flagged,
            suspicious  = flagged.filter { it.suspicious },
        )
    }

    private fun parseNetFile(path: String, proto: String): List<NetworkConnection> {
        val file = java.io.File(path)
        if (!file.exists()) return emptyList()

        return try {
            file.readLines()
                .drop(1)                           // header row
                .mapNotNull { line ->
                    try { parseLine(line.trim(), proto) } catch (_: Exception) { null }
                }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to read $path", e)
            emptyList()
        }
    }

    /**
     * Parse a single line from `/proc/net/tcp`.
     *
     * Format (space-separated columns):
     *   sl local_address rem_address st tx_queue:rx_queue tr:tm->when retrnsmt uid timeout inode
     */
    private fun parseLine(line: String, proto: String): NetworkConnection? {
        val parts = line.split(Regex("\\s+"))
        if (parts.size < 10) return null

        val localHex  = parts[1]
        val remoteHex = parts[2]
        val stateHex  = parts[3]
        val uid       = parts[7].toIntOrNull() ?: -1

        val (localAddr, localPort)   = parseHexAddr(localHex)   ?: return null
        val (remoteAddr, remotePort) = parseHexAddr(remoteHex)  ?: return null
        val state = TCP_STATES[stateHex] ?: stateHex

        return NetworkConnection(
            protocol   = proto,
            localAddr  = localAddr,
            localPort  = localPort,
            remoteAddr = remoteAddr,
            remotePort = remotePort,
            state      = state,
            pid        = null,  // pid-to-socket mapping requires /proc/<pid>/fd scanning (root)
        )
    }

    /**
     * Convert a `XXXXXXXX:PPPP` hex address to (ip, port).
     * The address is little-endian for IPv4.
     */
    private fun parseHexAddr(hex: String): Pair<String, Int>? {
        val colon = hex.indexOf(':')
        if (colon < 0) return null

        val addrHex = hex.substring(0, colon)
        val portHex = hex.substring(colon + 1)

        val port = portHex.toIntOrNull(16) ?: return null

        val ip = if (addrHex.length == 8) {
            // IPv4 — little-endian 32-bit
            val n = addrHex.toLongOrNull(16) ?: return null
            listOf(
                (n and 0xFF).toInt(),
                ((n shr 8) and 0xFF).toInt(),
                ((n shr 16) and 0xFF).toInt(),
                ((n shr 24) and 0xFF).toInt(),
            ).joinToString(".")
        } else {
            // IPv6 — 4 little-endian 32-bit words
            addrHex.chunked(8)
                .map { word ->
                    val n = word.toLongOrNull(16) ?: return null
                    "%02x%02x:%02x%02x".format(
                        (n and 0xFF).toInt(),
                        ((n shr 8) and 0xFF).toInt(),
                        ((n shr 16) and 0xFF).toInt(),
                        ((n shr 24) and 0xFF).toInt(),
                    )
                }
                .joinToString(":")
        }

        return ip to port
    }

    private fun flag(conn: NetworkConnection): NetworkConnection {
        // Listening on a suspicious port
        if (conn.state == "LISTEN" && conn.localPort in SUSPICIOUS_PORTS) {
            return conn.copy(suspicious = true)
        }
        // Outbound to a known C2/RAT port
        if (conn.state == "ESTABLISHED" && conn.remotePort in SUSPICIOUS_PORTS) {
            return conn.copy(suspicious = true)
        }
        // Any non-loopback LISTEN on a high port (> 10000) that isn't expected
        if (conn.state == "LISTEN" &&
            conn.localAddr !in LOOPBACK_ADDRS &&
            conn.localPort > 10000 &&
            conn.localPort !in EXPECTED_HIGH_PORTS
        ) {
            return conn.copy(suspicious = true)
        }
        return conn
    }

    companion object {
        private const val TAG = "NetworkMonitor"

        private val LOOPBACK_ADDRS = setOf("127.0.0.1", "0.0.0.0", "::1", "0000:0000:0000:0000:0000:0000:0000:0001")

        /** Classic backdoor / RAT / meterpreter ports. */
        private val SUSPICIOUS_PORTS = setOf(
            1337, 4444, 4445, 4446, 5555, 6666, 6667, 7777,
            8888, 9999, 31337, 12345, 54321,
        )

        /** High ports that are legitimately expected on Android. */
        private val EXPECTED_HIGH_PORTS = setOf(
            62001, 62002,   // ADB
        )

        private val TCP_STATES = mapOf(
            "01" to "ESTABLISHED", "02" to "SYN_SENT",  "03" to "SYN_RECV",
            "04" to "FIN_WAIT1",   "05" to "FIN_WAIT2", "06" to "TIME_WAIT",
            "07" to "CLOSE",       "08" to "CLOSE_WAIT", "09" to "LAST_ACK",
            "0A" to "LISTEN",      "0B" to "CLOSING",
        )
    }
}
