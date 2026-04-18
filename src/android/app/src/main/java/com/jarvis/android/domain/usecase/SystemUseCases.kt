package com.jarvis.android.domain.usecase

import com.jarvis.android.domain.model.AppInfo
import com.jarvis.android.domain.model.ProcessInfo
import com.jarvis.android.domain.model.SystemInfo
import com.jarvis.android.domain.repository.SystemRepository
import javax.inject.Inject

class GetSystemInfoUseCase @Inject constructor(private val repo: SystemRepository) {
    suspend operator fun invoke(): SystemInfo = repo.getSystemInfo()
}

class GetProcessesUseCase @Inject constructor(private val repo: SystemRepository) {
    suspend operator fun invoke(limit: Int = 30): List<ProcessInfo> = repo.getProcesses(limit)
}

class KillProcessUseCase @Inject constructor(private val repo: SystemRepository) {
    suspend operator fun invoke(pid: Int, signal: String = "SIGTERM"): Result<Unit> =
        repo.killProcess(pid, signal)
}

class GetInstalledAppsUseCase @Inject constructor(private val repo: SystemRepository) {
    suspend operator fun invoke(userOnly: Boolean = true): List<AppInfo> =
        repo.getInstalledApps(userOnly)
}

class GetLogcatUseCase @Inject constructor(private val repo: SystemRepository) {
    suspend operator fun invoke(
        lines: Int = 100, tag: String? = null, level: String = "V",
    ): List<String> = repo.getLogcat(lines, tag, level)
}

class ExecuteCommandUseCase @Inject constructor(private val repo: SystemRepository) {
    suspend operator fun invoke(command: String, asRoot: Boolean = false): String =
        repo.executeCommand(command, asRoot)
}
