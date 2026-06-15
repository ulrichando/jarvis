package com.jarvis.android.data.repository

import android.content.Context
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.os.BatteryManager
import android.os.Build
import android.os.PowerManager
import com.jarvis.android.domain.model.AppInfo
import com.jarvis.android.domain.model.ProcessInfo
import com.jarvis.android.domain.model.SystemInfo
import com.jarvis.android.domain.repository.SystemRepository
import com.jarvis.android.system.root.RootManager
import com.jarvis.android.system.root.RootShell
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class SystemRepositoryImpl @Inject constructor(
    @ApplicationContext private val context: Context,
    private val rootShell: RootShell,
    private val rootManager: RootManager,
) : SystemRepository {

    override suspend fun getSystemInfo(): SystemInfo {
        val am = context.getSystemService(Context.ACTIVITY_SERVICE) as android.app.ActivityManager
        val mi = android.app.ActivityManager.MemoryInfo().also { am.getMemoryInfo(it) }
        val bm = context.getSystemService(Context.BATTERY_SERVICE) as BatteryManager

        return SystemInfo(
            deviceModel     = "${Build.MANUFACTURER} ${Build.MODEL}",
            androidVersion  = Build.VERSION.RELEASE,
            sdkInt          = Build.VERSION.SDK_INT,
            arch            = Build.SUPPORTED_ABIS.firstOrNull() ?: "unknown",
            ramTotalMb      = mi.totalMem / 1_048_576,
            ramAvailMb      = mi.availMem / 1_048_576,
            ramLowMemory    = mi.lowMemory,
            batteryPct      = bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY),
            batteryCharging = bm.isCharging,
            uptimeMs        = android.os.SystemClock.elapsedRealtime(),
            kernelVersion   = System.getProperty("os.version") ?: "unknown",
            isRooted        = rootManager.isRooted,
        )
    }

    override suspend fun getProcesses(limit: Int): List<ProcessInfo> {
        val cmd = if (rootManager.isRooted) {
            "ps -A -o PID,PPID,USER,RSS,PCPU,NAME 2>/dev/null | tail -n +2 | head -$limit"
        } else {
            "ps -A -o PID,PPID,USER,RSS,NAME 2>/dev/null | tail -n +2 | head -$limit"
        }
        val result = rootShell.exec(cmd, asRoot = rootManager.isRooted)
        return result.stdout.mapNotNull { line -> parseProcessLine(line, rootManager.isRooted) }
    }

    private fun parseProcessLine(line: String, hasCpu: Boolean): ProcessInfo? {
        val parts = line.trim().split(Regex("\\s+"))
        return try {
            if (hasCpu && parts.size >= 6) {
                ProcessInfo(
                    pid        = parts[0].toInt(),
                    ppid       = parts[1].toInt(),
                    user       = parts[2],
                    rssKb      = parts[3].toLong(),
                    cpuPercent = parts[4].toFloat(),
                    name       = parts[5],
                )
            } else if (!hasCpu && parts.size >= 5) {
                ProcessInfo(
                    pid        = parts[0].toInt(),
                    ppid       = parts[1].toInt(),
                    user       = parts[2],
                    rssKb      = parts[3].toLong(),
                    cpuPercent = 0f,
                    name       = parts[4],
                )
            } else null
        } catch (_: Exception) { null }
    }

    override suspend fun killProcess(pid: Int, signal: String): Result<Unit> {
        val result = rootShell.exec("kill -$signal $pid 2>&1", asRoot = rootManager.isRooted)
        return if (result.isSuccess) Result.success(Unit)
               else Result.failure(RuntimeException(result.stderr.joinToString("; ")))
    }

    override suspend fun getInstalledApps(userOnly: Boolean): List<AppInfo> {
        val pm = context.packageManager
        val flags = PackageManager.GET_META_DATA
        val packages = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            pm.getInstalledApplications(PackageManager.ApplicationInfoFlags.of(flags.toLong()))
        } else {
            @Suppress("DEPRECATION")
            pm.getInstalledApplications(flags)
        }
        return packages
            .filter { !userOnly || (it.flags and ApplicationInfo.FLAG_SYSTEM) == 0 }
            .sortedBy { it.packageName }
            .map { info ->
                val label = try { pm.getApplicationLabel(info).toString() } catch (_: Exception) { info.packageName }
                val ver   = try { pm.getPackageInfo(info.packageName, 0).versionName } catch (_: Exception) { null }
                AppInfo(
                    packageName = info.packageName,
                    label       = label,
                    isSystem    = (info.flags and ApplicationInfo.FLAG_SYSTEM) != 0,
                    versionName = ver,
                    targetSdk   = info.targetSdkVersion,
                )
            }
    }

    override suspend fun getLogcat(lines: Int, tag: String?, level: String): List<String> {
        val filter = if (tag != null) "$tag:$level *:S" else "*:$level"
        val result = rootShell.exec(
            "logcat -d -t $lines $filter 2>&1",
            asRoot = rootManager.isRooted,
        )
        return result.stdout
    }

    override suspend fun executeCommand(command: String, asRoot: Boolean): String =
        rootShell.exec(command, asRoot = asRoot && rootManager.isRooted).toToolResultText()
}
