package com.jarvis.android.domain.usecase.cyber

import com.jarvis.android.domain.model.HttpInspectResult
import com.jarvis.android.domain.model.LogEntry
import com.jarvis.android.domain.model.NetworkSnapshot
import com.jarvis.android.domain.model.PortScanResult
import com.jarvis.android.domain.model.ProcessSnapshot
import com.jarvis.android.domain.repository.CyberRepository
import kotlinx.coroutines.flow.Flow
import javax.inject.Inject

// ── Red Team ──────────────────────────────────────────────────────────────────

class PortScanUseCase @Inject constructor(private val repo: CyberRepository) {
    suspend operator fun invoke(
        target:      String,
        ports:       List<Int>? = null,
        grabBanners: Boolean    = true,
        onProgress:  (Int, Int) -> Unit = { _, _ -> },
    ): PortScanResult = repo.portScan(target, ports, grabBanners, onProgress)
}

class HttpInspectUseCase @Inject constructor(private val repo: CyberRepository) {
    suspend operator fun invoke(url: String): HttpInspectResult = repo.httpInspect(url)
}

// ── Blue Team ─────────────────────────────────────────────────────────────────

class GetProcessSnapshotUseCase @Inject constructor(private val repo: CyberRepository) {
    suspend operator fun invoke(): ProcessSnapshot = repo.getProcessSnapshot()
}

class GetNetworkSnapshotUseCase @Inject constructor(private val repo: CyberRepository) {
    suspend operator fun invoke(): NetworkSnapshot = repo.getNetworkSnapshot()
}

class WatchLogsUseCase @Inject constructor(private val repo: CyberRepository) {
    operator fun invoke(filterTag: String? = null): Flow<LogEntry> = repo.watchLogs(filterTag)
}
