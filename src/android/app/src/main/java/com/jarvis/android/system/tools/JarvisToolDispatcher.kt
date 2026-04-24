package com.jarvis.android.system.tools

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.hardware.SensorManager
import android.net.wifi.WifiManager
import android.os.BatteryManager
import android.os.Build
import android.os.Debug
import android.os.Process
import android.util.Log
import com.jarvis.android.domain.model.RoutingMode
import com.jarvis.android.domain.repository.ModelRepository
import com.jarvis.android.system.llm.GenerationConfig
import com.jarvis.android.system.root.RootManager
import com.jarvis.android.system.root.RootShell
import com.jarvis.android.system.root.ShellResult
import com.jarvis.android.system.terminal.TerminalSessionManager
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import java.io.File
import java.io.RandomAccessFile
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Routes Claude [ToolUseBlock] calls to Android system actions and returns
 * [ToolResultBlock] payloads back into the conversation loop.
 *
 * ## Supported tools (must match `jarvis_persona.txt` declarations)
 *
 * | Tool name          | Description                                          |
 * |--------------------|------------------------------------------------------|
 * | bash_exec          | Run a shell command (root optional)                  |
 * | read_file          | Read a file path (root-aware via `cat`)              |
 * | write_file         | Write content to a path (root optional)              |
 * | list_directory     | List a directory (depth 1, optionally recursive)     |
 * | get_system_info    | CPU/RAM/battery/uptime/device snapshot               |
 * | list_processes     | Top N processes by CPU or RAM                        |
 * | kill_process       | Send SIGTERM/SIGKILL to a PID (root required)        |
 * | list_installed_apps| Enumerate packages (all or user-only)                |
 * | get_logcat         | Fetch recent logcat lines with optional tag filter   |
 * | network_scan       | List visible WiFi SSIDs and signal levels            |
 * | get_sensors        | Read a snapshot of device sensor values              |
 * | terminal_create    | Open a new PTY session                               |
 * | terminal_write     | Write text to an existing PTY session                |
 * | terminal_kill      | Close a PTY session                                  |
 * | set_clipboard      | Write a string to the system clipboard               |
 * | get_clipboard      | Read the current clipboard text                      |
 * | list_local_models  | List downloaded on-device models and their state     |
 * | load_local_model   | Load a model into GPU/RAM by ID                      |
 * | run_local_inference| Run a single-turn inference against the loaded model |
 * | download_model     | Enqueue a background download for a catalog model    |
 * | benchmark_model    | Run the 200-token benchmark on the loaded model      |
 * | set_routing_mode   | Change the IntelliRouter routing mode (AUTO/LOCAL/…) |
 *
 * ## Dangerous-command gate
 * Before executing a destructive command (matched by [CONFIRM_PATTERNS]),
 * a [ConfirmationRequest] is emitted on [confirmationRequests]. The caller
 * **must** collect this flow and call [resolveConfirmation] with the user's
 * decision; the coroutine suspends until resolved.
 *
 * ## Output limits
 * All tool results are capped at [MAX_RESULT_CHARS] to stay within
 * Claude's context window.
 */
@Singleton
class JarvisToolDispatcher @Inject constructor(
    @ApplicationContext private val context: Context,
    private val rootShell: RootShell,
    private val rootManager: RootManager,
    private val sessionManager: TerminalSessionManager,
    private val modelRepository: ModelRepository,
) {

    private val json = Json { ignoreUnknownKeys = true; isLenient = true }

    // ── Confirmation flow ─────────────────────────────────────────────────

    private val _confirmationRequests = MutableSharedFlow<ConfirmationRequest>(extraBufferCapacity = 1)
    val confirmationRequests: SharedFlow<ConfirmationRequest> = _confirmationRequests.asSharedFlow()

    /** Pending confirmations keyed by requestId → CompletableDeferred<Boolean>. */
    private val pendingConfirmations =
        java.util.concurrent.ConcurrentHashMap<String, kotlinx.coroutines.CompletableDeferred<Boolean>>()

    /**
     * Called by the UI after the user taps Allow / Deny on a confirmation dialog.
     * Resolves the corresponding suspended [dispatch] call.
     */
    fun resolveConfirmation(requestId: String, allowed: Boolean) {
        pendingConfirmations.remove(requestId)?.complete(allowed)
    }

    // ── Main dispatch ─────────────────────────────────────────────────────

    /**
     * Execute a single Claude `tool_use` block and return a `tool_result`.
     *
     * @param block  The tool call from Claude's API response.
     * @return       [ToolResultBlock] to append to the next API request.
     */
    suspend fun dispatch(block: ToolUseBlock): ToolResultBlock {
        Log.d(TAG, "dispatch: tool=${block.name} id=${block.id}")
        return try {
            val content = when (block.name) {
                "bash_exec"          -> handleBashExec(block.input)
                "read_file"          -> handleReadFile(block.input)
                "write_file"         -> handleWriteFile(block.input)
                "list_directory"     -> handleListDirectory(block.input)
                "get_system_info"    -> handleGetSystemInfo()
                "list_processes"     -> handleListProcesses(block.input)
                "kill_process"       -> handleKillProcess(block.input)
                "list_installed_apps"-> handleListInstalledApps(block.input)
                "launch_app"         -> handleLaunchApp(block.input)
                "open_intent"        -> handleOpenIntent(block.input)
                "ui_dump"            -> handleUiDump(block.input)
                "ui_tap"             -> handleUiTap(block.input)
                "ui_swipe"           -> handleUiSwipe(block.input)
                "ui_type"            -> handleUiType(block.input)
                "ui_action"          -> handleUiAction(block.input)
                "get_logcat"         -> handleGetLogcat(block.input)
                "network_scan"       -> handleNetworkScan()
                "get_sensors"        -> handleGetSensors()
                "terminal_create"    -> handleTerminalCreate(block.input)
                "terminal_write"     -> handleTerminalWrite(block.input)
                "terminal_kill"      -> handleTerminalKill(block.input)
                "set_clipboard"      -> handleSetClipboard(block.input)
                "get_clipboard"      -> handleGetClipboard()
                // ── Local LLM (Module A) ──────────────────────────────────────
                "list_local_models"  -> handleListLocalModels()
                "load_local_model"   -> handleLoadLocalModel(block.input)
                "run_local_inference"-> handleRunLocalInference(block.input)
                "download_model"     -> handleDownloadModel(block.input)
                "benchmark_model"    -> handleBenchmarkModel()
                "set_routing_mode"   -> handleSetRoutingMode(block.input)
                else -> "error: unknown tool '${block.name}'"
            }
            ToolResultBlock(toolUseId = block.id, content = content.take(MAX_RESULT_CHARS))
        } catch (e: Exception) {
            Log.e(TAG, "dispatch error for ${block.name}: ${e.message}", e)
            ToolResultBlock(toolUseId = block.id, content = "error: ${e.message}", isError = true)
        }
    }

    // ── Tool: bash_exec ───────────────────────────────────────────────────

    private suspend fun handleBashExec(input: JsonObject): String {
        val command  = input.str("command") ?: return "error: missing 'command'"
        val asRoot   = input.bool("as_root") ?: false
        val timeoutMs = input.long("timeout_ms") ?: 30_000L

        if (requiresConfirmation(command)) {
            val allowed = awaitConfirmation(
                ConfirmationRequest(
                    toolName    = "bash_exec",
                    description = "Execute shell command",
                    detail      = command,
                )
            )
            if (!allowed) return "blocked: user denied execution of: $command"
        }

        val result = rootShell.exec(command, asRoot = asRoot, timeoutMs = timeoutMs)
        return result.toToolResultText()
    }

    // ── Tool: read_file ───────────────────────────────────────────────────

    private suspend fun handleReadFile(input: JsonObject): String {
        val path      = input.str("path") ?: return "error: missing 'path'"
        val maxBytes  = input.int("max_bytes") ?: 65536
        val asRoot    = input.bool("as_root") ?: false

        return if (asRoot && rootManager.isRooted) {
            // Read via root shell to bypass permissions
            val result = rootShell.exec("cat ${shellQuote(path)}", asRoot = true)
            if (result.isSuccess) {
                result.stdout.joinToString("\n").take(maxBytes)
            } else {
                "error: ${result.stderr.joinToString("; ")}"
            }
        } else {
            val file = File(path)
            when {
                !file.exists() -> "error: file not found: $path"
                !file.isFile   -> "error: not a file: $path"
                !file.canRead() -> "error: permission denied: $path"
                else -> file.readText(Charsets.UTF_8).take(maxBytes)
            }
        }
    }

    // ── Tool: write_file ──────────────────────────────────────────────────

    private suspend fun handleWriteFile(input: JsonObject): String {
        val path    = input.str("path")    ?: return "error: missing 'path'"
        val content = input.str("content") ?: return "error: missing 'content'"
        val asRoot  = input.bool("as_root") ?: false
        val append  = input.bool("append") ?: false

        val allowed = awaitConfirmation(
            ConfirmationRequest(
                toolName    = "write_file",
                description = if (append) "Append to file" else "Overwrite file",
                detail      = path,
            )
        )
        if (!allowed) return "blocked: user denied write to $path"

        return if (asRoot && rootManager.isRooted) {
            val op = if (append) ">>" else ">"
            val result = rootShell.exec("printf '%s' ${shellQuote(content)} $op ${shellQuote(path)}", asRoot = true)
            if (result.isSuccess) "ok: wrote ${content.length} chars to $path"
            else "error: ${result.stderr.joinToString("; ")}"
        } else {
            try {
                val file = File(path)
                file.parentFile?.mkdirs()
                if (append) file.appendText(content, Charsets.UTF_8)
                else        file.writeText(content, Charsets.UTF_8)
                "ok: wrote ${content.length} chars to $path"
            } catch (e: Exception) {
                "error: ${e.message}"
            }
        }
    }

    // ── Tool: list_directory ──────────────────────────────────────────────

    private suspend fun handleListDirectory(input: JsonObject): String {
        val path      = input.str("path") ?: "/"
        val recursive = input.bool("recursive") ?: false
        val asRoot    = input.bool("as_root") ?: false

        val cmd = if (recursive) "find ${shellQuote(path)} -maxdepth 3 -printf '%M %s %f %p\n' 2>/dev/null | head -500"
                  else           "ls -la ${shellQuote(path)} 2>&1"

        return if (asRoot && rootManager.isRooted) {
            rootShell.exec(cmd, asRoot = true).toToolResultText()
        } else {
            rootShell.exec(cmd, asRoot = false).toToolResultText()
        }
    }

    // ── Tool: get_system_info ─────────────────────────────────────────────

    private fun handleGetSystemInfo(): String {
        val sb = StringBuilder()

        // CPU info
        try {
            val stat = RandomAccessFile("/proc/stat", "r")
            val cpuLine = stat.readLine()
            stat.close()
            sb.appendLine("cpu_stat: $cpuLine")
        } catch (_: Exception) {}

        // Memory info
        try {
            val mi = android.app.ActivityManager.MemoryInfo()
            val am = context.getSystemService(Context.ACTIVITY_SERVICE) as android.app.ActivityManager
            am.getMemoryInfo(mi)
            sb.appendLine("ram_total_mb: ${mi.totalMem / 1_048_576}")
            sb.appendLine("ram_avail_mb: ${mi.availMem / 1_048_576}")
            sb.appendLine("ram_low_memory: ${mi.lowMemory}")
        } catch (_: Exception) {}

        // Battery
        try {
            val bm = context.getSystemService(Context.BATTERY_SERVICE) as BatteryManager
            val pct = bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
            val charging = bm.isCharging
            sb.appendLine("battery_pct: $pct")
            sb.appendLine("battery_charging: $charging")
        } catch (_: Exception) {}

        // Uptime
        sb.appendLine("uptime_ms: ${android.os.SystemClock.elapsedRealtime()}")

        // Device info
        sb.appendLine("device: ${Build.MODEL} (${Build.DEVICE})")
        sb.appendLine("android: ${Build.VERSION.RELEASE} (SDK ${Build.VERSION.SDK_INT})")
        sb.appendLine("arch: ${Build.SUPPORTED_ABIS.firstOrNull()}")
        sb.appendLine("root_available: ${rootManager.isRooted}")

        // Kernel
        sb.appendLine("kernel: ${System.getProperty("os.version")}")

        return sb.toString().trimEnd()
    }

    // ── Tool: list_processes ──────────────────────────────────────────────

    private suspend fun handleListProcesses(input: JsonObject): String {
        val limit  = input.int("limit")  ?: 30
        val sortBy = input.str("sort_by") ?: "cpu"  // "cpu" | "mem"

        // Use root ps for full process visibility if available
        val cmd = if (rootManager.isRooted) {
            "ps -A -o PID,PPID,USER,RSS,PCPU,NAME 2>/dev/null | head -${limit + 1}"
        } else {
            "ps -A -o PID,PPID,USER,RSS,NAME 2>/dev/null | head -${limit + 1}"
        }
        return rootShell.exec(cmd, asRoot = rootManager.isRooted).toToolResultText()
    }

    // ── Tool: kill_process ────────────────────────────────────────────────

    private suspend fun handleKillProcess(input: JsonObject): String {
        val pid    = input.int("pid")    ?: return "error: missing 'pid'"
        val signal = input.str("signal") ?: "SIGTERM"

        if (pid <= 0) return "error: invalid pid $pid"
        // Never kill our own process
        if (pid == Process.myPid()) return "error: refusing to kill own process"

        val allowed = awaitConfirmation(
            ConfirmationRequest(
                toolName    = "kill_process",
                description = "Send $signal to PID $pid",
                detail      = "kill -$signal $pid",
            )
        )
        if (!allowed) return "blocked: user denied kill of PID $pid"

        return if (rootManager.isRooted) {
            rootShell.exec("kill -$signal $pid 2>&1", asRoot = true).toToolResultText()
        } else {
            try {
                Process.killProcess(pid)
                "ok: sent $signal to PID $pid"
            } catch (e: Exception) {
                "error: ${e.message}"
            }
        }
    }

    // ── Tool: list_installed_apps ─────────────────────────────────────────

    private fun handleListInstalledApps(input: JsonObject): String {
        val userOnly = input.bool("user_only") ?: true
        val pm = context.packageManager
        val flags = PackageManager.GET_META_DATA
        val packages = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            pm.getInstalledApplications(PackageManager.ApplicationInfoFlags.of(flags.toLong()))
        } else {
            @Suppress("DEPRECATION")
            pm.getInstalledApplications(flags)
        }

        val filtered = if (userOnly) {
            packages.filter { it.flags and ApplicationInfo.FLAG_SYSTEM == 0 }
        } else {
            packages
        }

        val sb = StringBuilder()
        sb.appendLine("total: ${filtered.size}")
        filtered.sortedBy { it.packageName }.take(200).forEach { info ->
            val label = try { pm.getApplicationLabel(info).toString() } catch (_: Exception) { info.packageName }
            val system = if (info.flags and ApplicationInfo.FLAG_SYSTEM != 0) " [system]" else ""
            sb.appendLine("${info.packageName}  ($label)$system")
        }
        if (filtered.size > 200) sb.appendLine("[… ${filtered.size - 200} more]")
        return sb.toString().trimEnd()
    }

    // ── Tool: launch_app ──────────────────────────────────────────────────

    /**
     * Open an installed app. Accepts either an exact package name or a
     * fuzzy label query. Uses PackageManager.getLaunchIntentForPackage so
     * this works without MANAGE_ACTIVITY_TASKS — the app can launch any
     * other app it's permitted to see via QUERY_ALL_PACKAGES (already in
     * the manifest).
     */
    private fun handleLaunchApp(input: JsonObject): String {
        val pkgArg = input.str("package")?.trim().orEmpty()
        val query  = input.str("query")?.trim().orEmpty()
        if (pkgArg.isEmpty() && query.isEmpty())
            return "error: provide 'package' or 'query'"

        val pm = context.packageManager

        // 1. Resolve the target package. Exact wins; otherwise fuzzy label match.
        val targetPkg: String = if (pkgArg.isNotEmpty()) {
            pkgArg
        } else {
            val flags = PackageManager.GET_META_DATA
            val apps = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU)
                pm.getInstalledApplications(PackageManager.ApplicationInfoFlags.of(flags.toLong()))
            else @Suppress("DEPRECATION") pm.getInstalledApplications(flags)

            val q = query.lowercase()
            val scored = apps.mapNotNull { info ->
                val label = runCatching { pm.getApplicationLabel(info).toString() }.getOrNull() ?: info.packageName
                val l = label.lowercase()
                val p = info.packageName.lowercase()
                val score = when {
                    l == q || p == q                 -> 100
                    l.startsWith(q) || p.startsWith(q) -> 80
                    l.contains(q)  || p.contains(q)    -> 50
                    else                               -> 0
                }
                if (score > 0) Triple(score, label, info.packageName) else null
            }.sortedByDescending { it.first }

            if (scored.isEmpty()) return "error: no installed app matches '$query'"
            if (scored.size > 1 && scored[0].first == scored[1].first) {
                // Ambiguous tie — surface candidates so the model can retry.
                val list = scored.take(6).joinToString("\n") { "  ${it.third}  (${it.second})" }
                return "ambiguous match for '$query'. Candidates:\n$list"
            }
            scored.first().third
        }

        // 2. Build + fire the launch intent.
        val intent = pm.getLaunchIntentForPackage(targetPkg)
            ?: return "error: '$targetPkg' has no launcher activity (not installed or not user-facing)"
        intent.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
        return try {
            context.startActivity(intent)
            val label = runCatching {
                pm.getApplicationLabel(pm.getApplicationInfo(targetPkg, 0)).toString()
            }.getOrDefault(targetPkg)
            "launched: $targetPkg ($label)"
        } catch (e: Exception) {
            "error: failed to launch $targetPkg — ${e.message}"
        }
    }

    // ── Tools: UI automation via Accessibility Service ───────────────────

    private fun a11y(): com.jarvis.android.service.JarvisAccessibilityService? =
        com.jarvis.android.service.JarvisAccessibilityService.instance

    private val a11yHelp =
        "Jarvis Accessibility Service is not enabled. Tell the user to go " +
        "to Settings → Accessibility → Installed apps → JARVIS → On."

    private fun handleUiDump(input: JsonObject): String {
        val svc = a11y() ?: return "error: $a11yHelp"
        val maxDepth = input.int("max_depth") ?: 8
        return svc.dumpUi(maxDepth)
    }

    private fun handleUiTap(input: JsonObject): String {
        val svc = a11y() ?: return "error: $a11yHelp"
        val text = input.str("text")
        if (!text.isNullOrBlank()) return svc.tapByText(text)
        val x = input.doubleOrNull("x")
        val y = input.doubleOrNull("y")
        if (x == null || y == null) return "error: provide 'text' or both 'x' and 'y'"
        val long = input.bool("long") ?: false
        return if (long) svc.longPressAt(x.toFloat(), y.toFloat())
               else      svc.tapAt(x.toFloat(), y.toFloat())
    }

    private fun handleUiSwipe(input: JsonObject): String {
        val svc = a11y() ?: return "error: $a11yHelp"
        val dur = (input.long("duration_ms") ?: 300L).coerceIn(50L, 3000L)
        val x1 = input.doubleOrNull("x1")
        val y1 = input.doubleOrNull("y1")
        val x2 = input.doubleOrNull("x2")
        val y2 = input.doubleOrNull("y2")
        if (x1 != null && y1 != null && x2 != null && y2 != null) {
            return svc.swipe(x1.toFloat(), y1.toFloat(), x2.toFloat(), y2.toFloat(), dur)
        }
        val dir = input.str("direction")?.lowercase()
            ?: return "error: provide 'direction' or explicit x1/y1/x2/y2"
        val dm = context.resources.displayMetrics
        val w  = dm.widthPixels.toFloat()
        val h  = dm.heightPixels.toFloat()
        val cx = w / 2f
        val cy = h / 2f
        val pts = when (dir) {
            "up"    -> floatArrayOf(cx, h * 0.8f, cx, h * 0.2f)
            "down"  -> floatArrayOf(cx, h * 0.2f, cx, h * 0.8f)
            "left"  -> floatArrayOf(w * 0.8f, cy, w * 0.2f, cy)
            "right" -> floatArrayOf(w * 0.2f, cy, w * 0.8f, cy)
            else -> return "error: direction must be up|down|left|right"
        }
        return svc.swipe(pts[0], pts[1], pts[2], pts[3], dur)
    }

    private fun handleUiType(input: JsonObject): String {
        val svc = a11y() ?: return "error: $a11yHelp"
        val text = input.str("text") ?: return "error: missing 'text'"
        return svc.inputText(text)
    }

    private fun handleUiAction(input: JsonObject): String {
        val svc = a11y() ?: return "error: $a11yHelp"
        val action = input.str("action") ?: return "error: missing 'action'"
        return svc.globalAction(action)
    }

    // ── Tool: open_intent ─────────────────────────────────────────────────

    /**
     * Fire an arbitrary Intent. Unlocks deep links and Settings sub-screens
     * for any app that advertises them. Refuses dangerous actions (factory
     * reset, device admin, etc.) without confirmation.
     */
    private suspend fun handleOpenIntent(input: JsonObject): String {
        val uriStr  = input.str("uri")?.trim().orEmpty()
        val action  = input.str("action")?.trim().orEmpty()
        val pkg     = input.str("package")?.trim().orEmpty()
        val mime    = input.str("mime_type")?.trim().orEmpty()
        val extras  = input["extras"] as? JsonObject

        if (uriStr.isEmpty() && action.isEmpty())
            return "error: provide at least 'uri' or 'action'"

        // Cheap guardrail — a few Settings actions are destructive/scary and
        // deserve the same confirmation gate bash_exec uses for `rm -rf`.
        val scary = action in setOf(
            "android.settings.ACTION_FACTORY_RESET",
            "android.settings.DEVICE_INFO_SETTINGS",
        )
        if (scary) {
            val allowed = awaitConfirmation(
                ConfirmationRequest(
                    toolName    = "open_intent",
                    description = "Open potentially destructive Settings screen",
                    detail      = action,
                )
            )
            if (!allowed) return "blocked: user denied opening: $action"
        }

        val intent = android.content.Intent().apply {
            if (action.isNotEmpty()) this.action = action
            else                     this.action = android.content.Intent.ACTION_VIEW
            if (uriStr.isNotEmpty()) this.data = android.net.Uri.parse(uriStr)
            if (mime.isNotEmpty())   setDataAndType(data, mime)
            if (pkg.isNotEmpty())    setPackage(pkg)
            addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
            // Grant read-uri-permission when targeting content:// URIs so the
            // receiving app can actually open them.
            if (uriStr.startsWith("content://"))
                addFlags(android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION)
            extras?.forEach { (k, v) ->
                when (val prim = v as? kotlinx.serialization.json.JsonPrimitive) {
                    null -> { /* skip non-primitives silently */ }
                    else -> when {
                        prim.isString         -> putExtra(k, prim.content)
                        prim.booleanOrNull != null -> putExtra(k, prim.booleanOrNull!!)
                        prim.longOrNull    != null -> putExtra(k, prim.longOrNull!!)
                        prim.intOrNull     != null -> putExtra(k, prim.intOrNull!!)
                        else                  -> putExtra(k, prim.content)
                    }
                }
            }
        }

        return try {
            // Verify something can handle it before firing — avoids a silent
            // ANR when no app responds.
            val resolved = context.packageManager.resolveActivity(intent, 0)
            if (resolved == null) {
                "error: no activity can handle this intent (action=${intent.action}, uri=${intent.data})"
            } else {
                context.startActivity(intent)
                val target = resolved.activityInfo?.let { "${it.packageName}/${it.name}" } ?: "resolved"
                "opened: $target (action=${intent.action}, uri=${intent.data ?: "∅"})"
            }
        } catch (e: Exception) {
            "error: startActivity failed — ${e.message}"
        }
    }

    // ── Tool: get_logcat ──────────────────────────────────────────────────

    private suspend fun handleGetLogcat(input: JsonObject): String {
        val lines   = input.int("lines")      ?: 100
        val tag     = input.str("tag")
        val level   = input.str("level")      ?: "V"   // V D I W E F
        val asRoot  = input.bool("as_root")   ?: false

        val tagFilter = if (tag != null) "$tag:$level *:S" else "*:$level"
        val cmd = "logcat -d -t $lines $tagFilter 2>&1"
        return rootShell.exec(cmd, asRoot = asRoot && rootManager.isRooted).toToolResultText()
    }

    // ── Tool: network_scan ────────────────────────────────────────────────

    @Suppress("DEPRECATION")
    private fun handleNetworkScan(): String {
        val wm = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        val results = try { wm.scanResults } catch (_: Exception) { emptyList() }

        if (results.isEmpty()) return "no_scan_results: ensure location permission is granted and WiFi is enabled"

        val sb = StringBuilder()
        sb.appendLine("wifi_networks: ${results.size}")
        results.sortedByDescending { it.level }.take(50).forEach { r ->
            val ssid = r.SSID.ifBlank { "<hidden>" }
            sb.appendLine("ssid=${ssid}  bssid=${r.BSSID}  rssi=${r.level}dBm  freq=${r.frequency}MHz  caps=${r.capabilities}")
        }
        return sb.toString().trimEnd()
    }

    // ── Tool: get_sensors ─────────────────────────────────────────────────

    private fun handleGetSensors(): String {
        val sm = context.getSystemService(Context.SENSOR_SERVICE) as SensorManager
        val sensors = sm.getSensorList(android.hardware.Sensor.TYPE_ALL)
        val sb = StringBuilder()
        sb.appendLine("sensor_count: ${sensors.size}")
        sensors.take(50).forEach { s ->
            sb.appendLine("name=${s.name}  type=${s.type}  vendor=${s.vendor}  maxRange=${s.maximumRange}  power=${s.power}mA")
        }
        if (sensors.size > 50) sb.appendLine("[… ${sensors.size - 50} more]")
        return sb.toString().trimEnd()
    }

    // ── Tool: terminal_create ─────────────────────────────────────────────

    private suspend fun handleTerminalCreate(input: JsonObject): String {
        val name   = input.str("name")    ?: "sh"
        val asRoot = input.bool("as_root") ?: false
        val rows   = input.int("rows")    ?: 24
        val cols   = input.int("cols")    ?: 80

        val session = sessionManager.createSession(
            name   = name,
            asRoot = asRoot,
            rows   = rows,
            cols   = cols,
        ) ?: return "error: could not create terminal session (max sessions reached or PTY alloc failed)"

        return "ok: session_id=${session.id} name=${session.name} pid=${session.childPid} root=${session.isRoot}"
    }

    // ── Tool: terminal_write ──────────────────────────────────────────────

    private fun handleTerminalWrite(input: JsonObject): String {
        val sessionId = input.str("session_id") ?: return "error: missing 'session_id'"
        val text      = input.str("text")       ?: return "error: missing 'text'"

        val session = sessionManager.getSession(sessionId)
            ?: return "error: session not found: $sessionId"
        if (!session.isAlive) return "error: session is dead: $sessionId"

        sessionManager.write(sessionId, text)
        return "ok: wrote ${text.length} chars to session $sessionId"
    }

    // ── Tool: terminal_kill ───────────────────────────────────────────────

    private suspend fun handleTerminalKill(input: JsonObject): String {
        val sessionId = input.str("session_id") ?: return "error: missing 'session_id'"

        sessionManager.getSession(sessionId)
            ?: return "error: session not found: $sessionId"

        sessionManager.killSession(sessionId)
        return "ok: killed session $sessionId"
    }

    // ── Tool: set_clipboard ───────────────────────────────────────────────

    private fun handleSetClipboard(input: JsonObject): String {
        val text  = input.str("text")  ?: return "error: missing 'text'"
        val label = input.str("label") ?: "JARVIS"

        val cm = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        cm.setPrimaryClip(ClipData.newPlainText(label, text))
        return "ok: clipboard set (${text.length} chars)"
    }

    // ── Tool: get_clipboard ───────────────────────────────────────────────

    private fun handleGetClipboard(): String {
        val cm = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val clip = cm.primaryClip ?: return "empty: no clipboard content"
        if (clip.itemCount == 0) return "empty: no clipboard items"
        val text = clip.getItemAt(0).coerceToText(context).toString()
        return text.take(MAX_RESULT_CHARS)
    }

    // ── Tool: list_local_models ───────────────────────────────────────────

    private suspend fun handleListLocalModels(): String {
        val models   = modelRepository.observeModels().first()
        val loadedId = modelRepository.observeLoadedModelId().value

        if (models.isEmpty()) return "no_models: catalog is empty — run refresh_catalog first"

        val sb = StringBuilder()
        sb.appendLine("total: ${models.size}")
        models.forEach { m ->
            val loaded = if (m.id == loadedId) " [LOADED]" else ""
            sb.appendLine("id=${m.id}  name=${m.name}  size=${m.sizeBytes / 1_048_576}MB  state=${m.downloadState::class.simpleName}$loaded  backend=${m.backend.label}")
        }
        return sb.toString().trimEnd()
    }

    // ── Tool: load_local_model ────────────────────────────────────────────

    private suspend fun handleLoadLocalModel(input: JsonObject): String {
        val modelId = input.str("model_id") ?: return "error: missing 'model_id'"

        val sb = StringBuilder()
        try {
            modelRepository.loadModel(modelId).collect { status ->
                sb.appendLine(status)
            }
        } catch (e: Exception) {
            return "error: ${e.message}"
        }
        return sb.toString().trimEnd().ifBlank { "ok: model $modelId loaded" }
    }

    // ── Tool: run_local_inference ─────────────────────────────────────────

    private suspend fun handleRunLocalInference(input: JsonObject): String {
        val modelId     = input.str("model_id")
            ?: modelRepository.observeLoadedModelId().value
            ?: return "error: no model loaded — call load_local_model first"
        val prompt      = input.str("prompt")      ?: return "error: missing 'prompt'"
        val maxTokens   = input.int("max_tokens")  ?: 256
        val temperature = (input["temperature"]?.jsonPrimitive?.content?.toFloatOrNull()) ?: 0.7f

        val config = GenerationConfig(
            maxNewTokens = maxTokens,
            temperature  = temperature,
            topK         = 40,
        )

        val sb = StringBuilder()
        try {
            modelRepository.generate(modelId, prompt, config).collect { token ->
                sb.append(token)
            }
        } catch (e: Exception) {
            return "error: ${e.message}"
        }
        return sb.toString().ifBlank { "ok: (empty response)" }
    }

    // ── Tool: download_model ──────────────────────────────────────────────

    private suspend fun handleDownloadModel(input: JsonObject): String {
        val modelId = input.str("model_id") ?: return "error: missing 'model_id'"
        return try {
            modelRepository.startDownload(modelId)
            "ok: download enqueued for $modelId — observe via list_local_models"
        } catch (e: Exception) {
            "error: ${e.message}"
        }
    }

    // ── Tool: benchmark_model ─────────────────────────────────────────────

    private suspend fun handleBenchmarkModel(): String {
        val modelId = modelRepository.observeLoadedModelId().value
            ?: return "error: no model loaded — call load_local_model first"
        return try {
            val r = modelRepository.benchmark(modelId)
            buildString {
                appendLine("model: ${r.modelName}")
                appendLine("tps: ${"%.2f".format(r.tokensPerSec)}")
                appendLine("ttft_ms: ${r.ttftMs}")
                appendLine("peak_ram_mb: ${r.peakRamMb}")
                appendLine("peak_cpu_pct: ${r.peakCpuPct}")
                appendLine("gpu_layers: ${r.gpuLayers}")
                appendLine("total_tokens: ${r.totalTokens}")
            }.trimEnd()
        } catch (e: Exception) {
            "error: ${e.message}"
        }
    }

    // ── Tool: set_routing_mode ────────────────────────────────────────────

    private suspend fun handleSetRoutingMode(input: JsonObject): String {
        val modeStr = input.str("mode")?.uppercase()
            ?: return "error: missing 'mode' — valid values: AUTO, LOCAL, CLOUD, HYBRID"
        val mode = RoutingMode.entries.find { it.name == modeStr }
            ?: return "error: unknown routing mode '$modeStr' — valid: AUTO, LOCAL, CLOUD, HYBRID"
        modelRepository.setRoutingMode(mode)
        return "ok: routing mode set to ${mode.label}"
    }

    // ── Confirmation helpers ──────────────────────────────────────────────

    private fun requiresConfirmation(command: String): Boolean {
        val trimmed = command.trim()
        return CONFIRM_PATTERNS.any { it.containsMatchIn(trimmed) }
    }

    /**
     * Emits a [ConfirmationRequest] and suspends until [resolveConfirmation] is called.
     * Times out after 60 seconds (user walked away).
     */
    private suspend fun awaitConfirmation(request: ConfirmationRequest): Boolean {
        val deferred = kotlinx.coroutines.CompletableDeferred<Boolean>()
        val reqWithId = request.copy(id = java.util.UUID.randomUUID().toString())
        pendingConfirmations[reqWithId.id] = deferred
        _confirmationRequests.emit(reqWithId)

        return try {
            kotlinx.coroutines.withTimeout(60_000L) { deferred.await() }
        } catch (_: kotlinx.coroutines.TimeoutCancellationException) {
            pendingConfirmations.remove(reqWithId.id)
            Log.w(TAG, "Confirmation timed out for ${request.toolName}")
            false
        }
    }

    // ── JSON helpers ──────────────────────────────────────────────────────

    private fun JsonObject.str(key: String)  = this[key]?.jsonPrimitive?.content
    private fun JsonObject.bool(key: String) = this[key]?.jsonPrimitive?.booleanOrNull
    private fun JsonObject.int(key: String)  = this[key]?.jsonPrimitive?.intOrNull
    private fun JsonObject.long(key: String) = this[key]?.jsonPrimitive?.longOrNull
    private fun JsonObject.doubleOrNull(key: String): Double? =
        this[key]?.jsonPrimitive?.content?.toDoubleOrNull()

    /** Wrap a string in single quotes and escape embedded single quotes for POSIX shells. */
    private fun shellQuote(s: String): String = "'${s.replace("'", "'\\''")}'"

    // ── Constants ─────────────────────────────────────────────────────────

    internal companion object {
        const val TAG             = "JarvisToolDispatcher"
        const val MAX_RESULT_CHARS = 8_000

        /**
         * Commands that require explicit user confirmation before execution.
         * Belt-and-suspenders over the [RootShell] denylist — this gate fires
         * first and waits for user input rather than hard-blocking.
         */
        val CONFIRM_PATTERNS = listOf(
            Regex("""(?i)\brm\s+-[rf]{1,2}\s"""),                // rm -rf / rm -r
            Regex("""(?i)\brmdir\b"""),
            Regex("""(?i)\bmkfs\b"""),
            Regex("""(?i)\bdd\b.+\bof=\s*/dev/"""),             // dd to block device
            Regex("""(?i)\bchmod\s+[0-7]{3,4}\s+/"""),          // chmod system paths
            Regex("""(?i)\bchown\b.+\s/"""),
            Regex("""(?i)\bmount\b"""),
            Regex("""(?i)\bumount\b"""),
            Regex("""(?i)\breboot\b"""),
            Regex("""(?i)\bpoweroff\b|\bshutdown\b"""),
            Regex("""(?i)\bflash\b"""),
            Regex("""(?i)\bwipe\b"""),
            Regex("""(?i)\bsystemctl\b"""),
            Regex("""(?i)\bmagisk\b"""),
        )
    }
}

// ── Data types ────────────────────────────────────────────────────────────────

/**
 * A single `tool_use` block from Claude's API response.
 *
 * Maps to the JSON shape:
 * ```json
 * { "type": "tool_use", "id": "toolu_01...", "name": "bash_exec", "input": { ... } }
 * ```
 */
@Serializable
data class ToolUseBlock(
    val id:    String,
    val name:  String,
    val input: JsonObject,
)

/**
 * A single `tool_result` block to include in the next API request.
 *
 * Maps to the JSON shape:
 * ```json
 * { "type": "tool_result", "tool_use_id": "toolu_01...", "content": "...", "is_error": false }
 * ```
 */
@Serializable
data class ToolResultBlock(
    val toolUseId: String,
    val content:   String,
    val isError:   Boolean = false,
)

/**
 * Emitted by [JarvisToolDispatcher] when a tool call requires explicit user approval.
 *
 * The UI should show a dialog with [description] and [detail], then call
 * [JarvisToolDispatcher.resolveConfirmation] with [id] and the user's decision.
 */
data class ConfirmationRequest(
    val id:          String = "",           // populated by awaitConfirmation
    val toolName:    String,
    val description: String,
    val detail:      String,
)
