package com.jarvis.android.presentation.root

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.util.Log
import androidx.core.content.FileProvider
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.android.system.root.RootManager
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import javax.inject.Inject

// ── UI State ──────────────────────────────────────────────────────────────────

sealed interface InstallerState {
    data object CheckingRoot                                            : InstallerState
    data class  AlreadyRooted(val provider: String)                    : InstallerState
    data object SelectTool                                             : InstallerState
    data class  FetchingRelease(val tool: RootTool)                    : InstallerState
    data class  ReadyToDownload(
        val tool:        RootTool,
        val version:     String,
        val downloadUrl: String,
        val sizeBytes:   Long,
    )                                                                   : InstallerState
    data class  Downloading(val tool: RootTool, val progress: Float)   : InstallerState
    data class  Downloaded(val tool: RootTool, val apkFile: File)      : InstallerState
    data class  Error(val message: String)                             : InstallerState
}

enum class RootTool(val label: String, val description: String) {
    MAGISK(
        label       = "Magisk",
        description = "The most widely used root solution. Downloads as an APK you can install directly to patch your boot image.",
    ),
    KERNELSU(
        label       = "KernelSU",
        description = "Kernel-based root — more stealthy, requires a compatible GKI kernel (Android 12+, most modern devices).",
    ),
}

// ── GitHub release DTOs ───────────────────────────────────────────────────────

@Serializable
private data class GhRelease(
    @SerialName("tag_name") val tagName: String,
    val assets: List<GhAsset> = emptyList(),
)

@Serializable
private data class GhAsset(
    val name:                String,
    @SerialName("browser_download_url") val downloadUrl: String,
    @SerialName("size")      val size: Long = 0L,
)

// ── ViewModel ─────────────────────────────────────────────────────────────────

@HiltViewModel
class RootInstallerViewModel @Inject constructor(
    @ApplicationContext private val context: Context,
    private val rootManager: RootManager,
    private val okHttpClient: OkHttpClient,
) : ViewModel() {

    private val _state = MutableStateFlow<InstallerState>(InstallerState.CheckingRoot)
    val state: StateFlow<InstallerState> = _state.asStateFlow()

    /** Human-readable device info shown on the selection screen. */
    val deviceInfo: String by lazy {
        "${Build.MANUFACTURER} ${Build.MODEL} — " +
        "Android ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT}) — " +
        Build.SUPPORTED_ABIS.firstOrNull()
    }

    private val json = Json { ignoreUnknownKeys = true; coerceInputValues = true }

    init {
        checkRootStatus()
    }

    // ── Root check ────────────────────────────────────────────────────────────

    fun checkRootStatus() {
        viewModelScope.launch {
            _state.value = InstallerState.CheckingRoot
            val rootState = withContext(Dispatchers.IO) {
                if (!rootManager.isRooted) rootManager.refreshState()
                rootManager.rootState.value
            }
            _state.value = when (rootState) {
                is com.jarvis.android.system.root.RootState.Granted -> {
                    val label = when (val p = rootState.provider) {
                        is com.jarvis.android.system.root.RootProvider.Magisk   -> "Magisk ${p.version}"
                        is com.jarvis.android.system.root.RootProvider.KernelSU -> "KernelSU ${p.version}"
                        is com.jarvis.android.system.root.RootProvider.Other    -> p.name
                        com.jarvis.android.system.root.RootProvider.Unknown     -> "Unknown root"
                    }
                    InstallerState.AlreadyRooted(label)
                }
                else -> InstallerState.SelectTool
            }
        }
    }

    // ── Fetch latest release ──────────────────────────────────────────────────

    fun fetchRelease(tool: RootTool) {
        viewModelScope.launch {
            _state.value = InstallerState.FetchingRelease(tool)
            try {
                val (version, url, size) = withContext(Dispatchers.IO) { queryGitHub(tool) }
                _state.value = InstallerState.ReadyToDownload(tool, version, url, size)
            } catch (e: Exception) {
                Log.e(TAG, "fetchRelease failed", e)
                _state.value = InstallerState.Error("Failed to fetch release: ${e.message}")
            }
        }
    }

    private fun queryGitHub(tool: RootTool): Triple<String, String, Long> {
        val repoPath = when (tool) {
            RootTool.MAGISK   -> "topjohnwu/Magisk"
            RootTool.KERNELSU -> "tiann/KernelSU"
        }
        val url = "https://api.github.com/repos/$repoPath/releases/latest"
        val request = Request.Builder()
            .url(url)
            .header("Accept", "application/vnd.github+json")
            .header("X-GitHub-Api-Version", "2022-11-28")
            .build()

        okHttpClient.newCall(request).execute().use { response ->
            val body = response.body?.string()
                ?: throw Exception("Empty response from GitHub")
            if (!response.isSuccessful) throw Exception("GitHub API error ${response.code}")

            val release = json.decodeFromString<GhRelease>(body)
            // Prefer .apk; fall back to .zip (KernelSU manager is always apk)
            val asset = release.assets.firstOrNull { it.name.endsWith(".apk") }
                ?: release.assets.firstOrNull { it.name.endsWith(".zip") }
                ?: throw Exception("No downloadable asset found in release ${release.tagName}")

            return Triple(release.tagName, asset.downloadUrl, asset.size)
        }
    }

    // ── Download ──────────────────────────────────────────────────────────────

    fun download(tool: RootTool, downloadUrl: String) {
        viewModelScope.launch {
            _state.value = InstallerState.Downloading(tool, 0f)
            try {
                val file = withContext(Dispatchers.IO) {
                    downloadFile(tool, downloadUrl) { progress ->
                        _state.value = InstallerState.Downloading(tool, progress)
                    }
                }
                _state.value = InstallerState.Downloaded(tool, file)
            } catch (e: Exception) {
                Log.e(TAG, "download failed", e)
                _state.value = InstallerState.Error("Download failed: ${e.message}")
            }
        }
    }

    private fun downloadFile(
        tool:        RootTool,
        url:         String,
        onProgress:  (Float) -> Unit,
    ): File {
        val dir = File(context.filesDir, "root_installers").also { it.mkdirs() }
        val ext = if (url.endsWith(".zip")) "zip" else "apk"
        val file = File(dir, "${tool.name.lowercase()}_installer.$ext")

        val request = Request.Builder().url(url).build()
        okHttpClient.newCall(request).execute().use { response ->
            if (!response.isSuccessful) throw Exception("HTTP ${response.code}")
            val body = response.body ?: throw Exception("Empty body")
            val total = body.contentLength().takeIf { it > 0 } ?: -1L
            var downloaded = 0L

            file.outputStream().use { out ->
                body.byteStream().use { input ->
                    val buf = ByteArray(8_192)
                    var read: Int
                    while (input.read(buf).also { read = it } != -1) {
                        out.write(buf, 0, read)
                        downloaded += read
                        if (total > 0) onProgress(downloaded.toFloat() / total.toFloat())
                    }
                }
            }
        }
        return file
    }

    // ── Install ───────────────────────────────────────────────────────────────

    /** Launch the system package installer for an APK downloaded to internal storage. */
    fun installApk(apkFile: File) {
        try {
            val uri: Uri = FileProvider.getUriForFile(
                context,
                "${context.packageName}.fileprovider",
                apkFile,
            )
            val intent = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(uri, "application/vnd.android.package-archive")
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            context.startActivity(intent)
        } catch (e: Exception) {
            Log.e(TAG, "installApk failed", e)
            _state.value = InstallerState.Error("Could not open installer: ${e.message}")
        }
    }

    fun reset() { _state.value = InstallerState.SelectTool }

    private companion object {
        const val TAG = "RootInstallerVM"
    }
}
