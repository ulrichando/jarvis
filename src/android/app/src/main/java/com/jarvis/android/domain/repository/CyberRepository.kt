package com.jarvis.android.domain.repository

import com.jarvis.android.domain.model.HttpInspectResult
import com.jarvis.android.domain.model.LogEntry
import com.jarvis.android.domain.model.NetworkSnapshot
import com.jarvis.android.domain.model.PortScanResult
import com.jarvis.android.domain.model.ProcessSnapshot
import kotlinx.coroutines.flow.Flow

/**
 * Repository interface for the JARVIS Cybersecurity Suite (Module C).
 *
 * Abstracts all Red-Team and Blue-Team operations behind a clean boundary
 * that ViewModels interact with.
 */
interface CyberRepository {

    // ── Red Team ──────────────────────────────────────────────────────────────

    /**
     * TCP connect scan of [target] over [ports].
     * Progress reported via [onProgress] callback.
     */
    suspend fun portScan(
        target:      String,
        ports:       List<Int>? = null,
        grabBanners: Boolean    = true,
        onProgress:  (scanned: Int, total: Int) -> Unit = { _, _ -> },
    ): PortScanResult

    /** Inspect HTTP/HTTPS response headers and TLS for [url]. */
    suspend fun httpInspect(url: String): HttpInspectResult

    // ── Blue Team ─────────────────────────────────────────────────────────────

    /** One-shot snapshot of running processes, with suspicious-process analysis. */
    suspend fun getProcessSnapshot(): ProcessSnapshot

    /** One-shot snapshot of active network connections, with anomaly analysis. */
    suspend fun getNetworkSnapshot(): NetworkSnapshot

    /**
     * Tail the device logcat stream as a cold [Flow].
     * Cancel the collection coroutine to stop streaming.
     */
    fun watchLogs(filterTag: String? = null): Flow<LogEntry>
}
