package com.jarvis.android.data.repository

import com.jarvis.android.domain.model.HttpInspectResult
import com.jarvis.android.domain.model.LogEntry
import com.jarvis.android.domain.model.NetworkSnapshot
import com.jarvis.android.domain.model.PortScanResult
import com.jarvis.android.domain.model.ProcessSnapshot
import com.jarvis.android.domain.repository.CyberRepository
import com.jarvis.android.system.cyber.HttpInspector
import com.jarvis.android.system.cyber.LogWatcher
import com.jarvis.android.system.cyber.NetworkMonitor
import com.jarvis.android.system.cyber.PortScanner
import com.jarvis.android.system.cyber.ProcessWatcher
import kotlinx.coroutines.flow.Flow
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class CyberRepositoryImpl @Inject constructor(
    private val portScanner:    PortScanner,
    private val httpInspector:  HttpInspector,
    private val processWatcher: ProcessWatcher,
    private val networkMonitor: NetworkMonitor,
    private val logWatcher:     LogWatcher,
) : CyberRepository {

    override suspend fun portScan(
        target:      String,
        ports:       List<Int>?,
        grabBanners: Boolean,
        onProgress:  (Int, Int) -> Unit,
    ): PortScanResult = portScanner.scan(
        target      = target,
        ports       = ports ?: PortScanner.COMMON_PORTS,
        grabBanners = grabBanners,
        onProgress  = onProgress,
    )

    override suspend fun httpInspect(url: String): HttpInspectResult =
        httpInspector.inspect(url)

    override suspend fun getProcessSnapshot(): ProcessSnapshot =
        processWatcher.snapshot()

    override suspend fun getNetworkSnapshot(): NetworkSnapshot =
        networkMonitor.snapshot()

    override fun watchLogs(filterTag: String?): Flow<LogEntry> =
        logWatcher.watch(filterTag = filterTag)
}
