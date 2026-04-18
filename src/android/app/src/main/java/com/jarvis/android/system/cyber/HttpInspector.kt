package com.jarvis.android.system.cyber

import android.util.Log
import com.jarvis.android.domain.model.HttpInspectResult
import com.jarvis.android.domain.model.SecurityHeaders
import com.jarvis.android.domain.model.TlsInfo
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.net.HttpURLConnection
import java.net.URL
import java.security.cert.X509Certificate
import java.text.SimpleDateFormat
import java.util.Locale
import javax.inject.Inject
import javax.inject.Singleton
import javax.net.ssl.HttpsURLConnection

/**
 * Inspects HTTP/HTTPS responses for security headers and TLS configuration.
 *
 * Follows up to [MAX_REDIRECTS] redirects, recording the chain. Does NOT
 * follow redirects automatically via [HttpURLConnection.setFollowRedirects]
 * so we can capture each hop's headers.
 */
@Singleton
class HttpInspector @Inject constructor() {

    suspend fun inspect(rawUrl: String): HttpInspectResult = withContext(Dispatchers.IO) {
        val start = System.currentTimeMillis()
        val url = rawUrl.trim().let {
            if (!it.startsWith("http://") && !it.startsWith("https://")) "https://$it" else it
        }

        try {
            doInspect(url, start)
        } catch (e: Exception) {
            Log.e(TAG, "Inspection failed for $url", e)
            HttpInspectResult(
                url             = url,
                statusCode      = -1,
                headers         = emptyMap(),
                securityHeaders = emptySecurityHeaders(),
                redirectChain   = emptyList(),
                tlsInfo         = null,
                durationMs      = System.currentTimeMillis() - start,
                error           = e.message,
            )
        }
    }

    private fun doInspect(originalUrl: String, start: Long): HttpInspectResult {
        val redirectChain = mutableListOf<String>()
        var currentUrl    = originalUrl
        var finalHeaders  = emptyMap<String, String>()
        var statusCode    = -1
        var tlsInfo: TlsInfo? = null
        var hops = 0

        while (hops <= MAX_REDIRECTS) {
            val conn = URL(currentUrl).openConnection() as HttpURLConnection
            conn.instanceFollowRedirects = false
            conn.connectTimeout = CONNECT_TIMEOUT_MS
            conn.readTimeout    = READ_TIMEOUT_MS
            conn.setRequestProperty("User-Agent", USER_AGENT)

            try {
                conn.connect()
                statusCode = conn.responseCode

                val headers = buildMap {
                    conn.headerFields.forEach { (k, v) ->
                        if (k != null && v.isNotEmpty()) put(k.lowercase(), v.first())
                    }
                }
                finalHeaders = headers

                if (currentUrl.startsWith("https://") && conn is HttpsURLConnection) {
                    tlsInfo = extractTlsInfo(conn)
                }

                val location = conn.getHeaderField("Location")
                if (statusCode in 300..399 && location != null) {
                    redirectChain.add(currentUrl)
                    currentUrl = if (location.startsWith("http")) location
                                 else URL(URL(currentUrl), location).toString()
                    hops++
                    conn.disconnect()
                    continue
                }
            } finally {
                conn.disconnect()
            }
            break
        }

        return HttpInspectResult(
            url             = originalUrl,
            statusCode      = statusCode,
            headers         = finalHeaders,
            securityHeaders = parseSecurityHeaders(finalHeaders),
            redirectChain   = redirectChain,
            tlsInfo         = tlsInfo,
            durationMs      = System.currentTimeMillis() - start,
        )
    }

    private fun extractTlsInfo(conn: HttpsURLConnection): TlsInfo? {
        return try {
            val cipher = conn.cipherSuite ?: "unknown"
            val cert   = conn.serverCertificates.firstOrNull() as? X509Certificate
            val fmt    = SimpleDateFormat("yyyy-MM-dd", Locale.US)
            // Infer protocol from cipher suite name (TLS_xxx → TLS 1.x)
            val protocol = when {
                cipher.startsWith("TLS_AES") || cipher.contains("CHACHA20") -> "TLSv1.3"
                cipher.startsWith("TLS_")    -> "TLSv1.2"
                cipher.startsWith("SSL_")    -> "SSLv3"
                else                         -> "TLS"
            }
            TlsInfo(
                protocol   = protocol,
                cipher     = cipher,
                issuer     = cert?.issuerDN?.name?.take(80) ?: "unknown",
                validUntil = cert?.notAfter?.let { fmt.format(it) } ?: "unknown",
            )
        } catch (_: Exception) { null }
    }

    private fun parseSecurityHeaders(h: Map<String, String>) = SecurityHeaders(
        hsts              = h.containsKey("strict-transport-security"),
        csp               = h.containsKey("content-security-policy"),
        xFrameOptions     = h.containsKey("x-frame-options"),
        xContentTypeNoSniff = h["x-content-type-options"]?.contains("nosniff") == true,
        referrerPolicy    = h.containsKey("referrer-policy"),
        permissionsPolicy = h.containsKey("permissions-policy") || h.containsKey("feature-policy"),
    )

    private fun emptySecurityHeaders() = SecurityHeaders(
        hsts = false, csp = false, xFrameOptions = false,
        xContentTypeNoSniff = false, referrerPolicy = false, permissionsPolicy = false,
    )

    companion object {
        private const val TAG                = "HttpInspector"
        private const val CONNECT_TIMEOUT_MS = 8_000
        private const val READ_TIMEOUT_MS    = 10_000
        private const val MAX_REDIRECTS      = 5
        private const val USER_AGENT         = "JARVIS-SecurityScanner/1.0"
    }
}
