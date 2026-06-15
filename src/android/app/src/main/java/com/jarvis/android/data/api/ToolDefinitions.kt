package com.jarvis.android.data.api

import com.jarvis.android.data.api.dto.ToolDefinitionDto
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject

/**
 * Compile-time definitions for all 16 JARVIS tools exposed to Claude.
 *
 * Each definition must exactly match the routing keys in [JarvisToolDispatcher].
 * The [inputSchema] is a JSON Schema `object` that the model uses to construct
 * well-typed `tool_use` input objects.
 *
 * Pass [ALL] to the `tools` field of [MessageRequestDto] on every request.
 */
object ToolDefinitions {

    val ALL: List<ToolDefinitionDto> by lazy {
        listOf(
            bashExec,
            readFile,
            writeFile,
            listDirectory,
            getSystemInfo,
            listProcesses,
            killProcess,
            listInstalledApps,
            launchApp,
            openIntent,
            uiDump,
            uiTap,
            uiSwipe,
            uiType,
            uiAction,
            getLogcat,
            networkScan,
            getSensors,
            terminalCreate,
            terminalWrite,
            terminalKill,
            setClipboard,
            getClipboard,
        )
    }

    // ── UI automation via AccessibilityService ────────────────────────────
    //
    // These five tools turn Jarvis into a UI automator: it can "see" the
    // current screen (ui_dump), tap on things (ui_tap), swipe (ui_swipe),
    // type into the focused field (ui_type), and fire system actions
    // (ui_action: back, home, recents, notifications). All require the
    // Jarvis Accessibility Service to be enabled in Settings.
    //
    // Typical workflow:
    //   1. call ui_dump → inspect the tree for the button/field you want
    //   2. call ui_tap {text:"…"} on the node, or ui_tap {x,y} with the
    //      coordinates from the dump bounds
    //   3. for text input: ui_tap into the EditText (or detect one that's
    //      already focused), then ui_type {text:"…"}

    private val uiDump = ToolDefinitionDto(
        name = "ui_dump",
        description = """
            Return a text summary of the current on-screen UI tree (the
            Accessibility snapshot). Each node has an index, class name,
            visible text, content-description, view id, bounds, and flags
            (C=clickable E=editable F=focused ✓=checked S=scrollable).
            Use this BEFORE ui_tap so you know what's actually on screen.
        """.trimIndent(),
        inputSchema = schema(
            required = emptyList(),
            properties = buildJsonObject {
                putJsonObject("max_depth") {
                    put("type", "integer")
                    put("description", "Maximum tree depth to walk. Default 8.")
                }
            },
        ),
    )

    private val uiTap = ToolDefinitionDto(
        name = "ui_tap",
        description = """
            Tap on a UI element. Provide either:
              • {text:"Send"}             — case-insensitive substring match on
                                            text or content-description
              • {x:540, y:1200}           — raw screen coordinates
              • {x:…, y:…, long:true}     — long-press at coordinates
            When text is given, Jarvis prefers ACTION_CLICK on the clickable
            ancestor (more reliable than a synthetic tap).
        """.trimIndent(),
        inputSchema = schema(
            required = emptyList(),
            properties = buildJsonObject {
                putJsonObject("text")  { put("type","string"); put("description","Visible label or content-description to match") }
                putJsonObject("x")     { put("type","number"); put("description","X coordinate in screen pixels") }
                putJsonObject("y")     { put("type","number"); put("description","Y coordinate in screen pixels") }
                putJsonObject("long")  { put("type","boolean"); put("description","true → long-press (~650 ms) at x,y") }
            },
        ),
    )

    private val uiSwipe = ToolDefinitionDto(
        name = "ui_swipe",
        description = """
            Swipe from one screen point to another. Accepts:
              • {direction:"up|down|left|right"}   — full-screen swipe in the
                                                    named direction
              • {x1,y1,x2,y2, duration?}           — explicit path in pixels
            Use this for scrolling, pull-to-refresh, swiping away dialogs, etc.
        """.trimIndent(),
        inputSchema = schema(
            required = emptyList(),
            properties = buildJsonObject {
                putJsonObject("direction") { put("type","string"); put("description","up|down|left|right (ignored if explicit coords given)") }
                putJsonObject("x1")        { put("type","number") }
                putJsonObject("y1")        { put("type","number") }
                putJsonObject("x2")        { put("type","number") }
                putJsonObject("y2")        { put("type","number") }
                putJsonObject("duration_ms") { put("type","integer"); put("description","Total swipe duration in ms. Default 300.") }
            },
        ),
    )

    private val uiType = ToolDefinitionDto(
        name = "ui_type",
        description = """
            Type text into the currently-focused editable field. If nothing
            is focused, the first EditText on screen receives the text.
            Always ui_tap the field first if you need a specific one.
        """.trimIndent(),
        inputSchema = schema(
            required = listOf("text"),
            properties = buildJsonObject {
                putJsonObject("text") { put("type","string"); put("description","Text to place in the field (replaces existing content).") }
            },
        ),
    )

    private val uiAction = ToolDefinitionDto(
        name = "ui_action",
        description = """
            Perform a system-level UI action: back, home, recents,
            notifications, quick_settings, power_dialog, split_screen,
            lock_screen.
        """.trimIndent(),
        inputSchema = schema(
            required = listOf("action"),
            properties = buildJsonObject {
                putJsonObject("action") {
                    put("type","string")
                    put("description","One of: back, home, recents, notifications, quick_settings, power_dialog, split_screen, lock_screen")
                }
            },
        ),
    )

    // ── open_intent ───────────────────────────────────────────────────────

    private val openIntent = ToolDefinitionDto(
        name = "open_intent",
        description = """
            Fire an arbitrary Android Intent to reach specific areas INSIDE an app or a
            system settings sub-screen. Use this for deep links, dialer, maps, Settings
            sub-pages, compose-SMS, Play Store pages, browser, etc.

            Common recipes:
              • Open a URL/deep link: {"uri":"https://instagram.com/<user>"}
              • Dial a number:        {"uri":"tel:+15551234567"}
              • Compose SMS:          {"uri":"smsto:+15551234567","extras":{"sms_body":"hi"}}
              • Email:                {"uri":"mailto:a@b.com?subject=hi"}
              • Maps search:          {"uri":"geo:0,0?q=Eiffel+Tower"}
              • Play Store listing:   {"uri":"market://details?id=com.foo"}
              • Android Settings:     {"action":"android.settings.SETTINGS"}
              • Wi-Fi settings:       {"action":"android.settings.WIFI_SETTINGS"}
              • App details:          {"action":"android.settings.APPLICATION_DETAILS_SETTINGS","uri":"package:com.foo"}
              • WhatsApp chat:        {"uri":"https://wa.me/15551234567?text=hello"}
              • Spotify search:       {"uri":"spotify:search:jazz","package":"com.spotify.music"}

            If 'package' is given, the intent is restricted to that app. Unknown schemes
            will fail cleanly with an error message.
        """.trimIndent(),
        inputSchema = schema(
            required = emptyList(),
            properties = buildJsonObject {
                putJsonObject("uri") {
                    put("type", "string")
                    put("description", "Intent data URI. Most common field. Schemes: https, http, tel, sms, smsto, mailto, geo, market, content, package, or app-specific (e.g. spotify:, whatsapp:, instagram:).")
                }
                putJsonObject("action") {
                    put("type", "string")
                    put("description", "Explicit Android Intent action, e.g. android.intent.action.VIEW, android.settings.WIFI_SETTINGS. Defaults to VIEW when 'uri' is given.")
                }
                putJsonObject("package") {
                    put("type", "string")
                    put("description", "Optional: restrict the intent to a specific app package. Leaves the system chooser out.")
                }
                putJsonObject("mime_type") {
                    put("type", "string")
                    put("description", "Optional MIME type, e.g. text/plain, image/*.")
                }
                putJsonObject("extras") {
                    put("type", "object")
                    put("description", "Optional key/value extras. Values may be strings, numbers, or booleans.")
                }
            },
        ),
    )

    // ── launch_app ────────────────────────────────────────────────────────

    private val launchApp = ToolDefinitionDto(
        name = "launch_app",
        description = """
            Open an installed Android app by package name or by a fuzzy match against
            the app's display label. Example: launch_app({package:"com.instagram.android"})
            or launch_app({query:"instagram"}). Prefer this tool over bash_exec/am-start —
            the app UID cannot invoke `am start` without MANAGE_ACTIVITY_TASKS.
        """.trimIndent(),
        inputSchema = schema(
            required = emptyList(),
            properties = buildJsonObject {
                putJsonObject("package") {
                    put("type", "string")
                    put("description", "Exact Android package name, e.g. com.instagram.android")
                }
                putJsonObject("query") {
                    put("type", "string")
                    put("description", "Substring of the app's display label, case-insensitive. Used only if 'package' is omitted.")
                }
            },
        ),
    )

    // ── bash_exec ─────────────────────────────────────────────────────────

    private val bashExec = ToolDefinitionDto(
        name = "bash_exec",
        description = """
            Execute a shell command on the device. Returns exit code, stdout, and stderr.
            Use `as_root: true` for commands that need root (su) — only works when Magisk/KernelSU
            is present and the app has been granted SU access.
            Commands matching destructive patterns require user confirmation before execution.
        """.trimIndent(),
        inputSchema = schema(
            required = listOf("command"),
            properties = buildJsonObject {
                putJsonObject("command") {
                    put("type", "string")
                    put("description", "Shell command to execute (passed to /system/bin/sh -c)")
                }
                putJsonObject("as_root") {
                    put("type", "boolean")
                    put("description", "Run as root via su. Default false.")
                }
                putJsonObject("timeout_ms") {
                    put("type", "integer")
                    put("description", "Execution timeout in milliseconds. Default 30000.")
                }
            },
        ),
    )

    // ── read_file ─────────────────────────────────────────────────────────

    private val readFile = ToolDefinitionDto(
        name = "read_file",
        description = """
            Read the contents of a file on the device filesystem.
            Use `as_root: true` to read protected files in /data, /system, etc.
        """.trimIndent(),
        inputSchema = schema(
            required = listOf("path"),
            properties = buildJsonObject {
                putJsonObject("path") {
                    put("type", "string")
                    put("description", "Absolute path to the file")
                }
                putJsonObject("max_bytes") {
                    put("type", "integer")
                    put("description", "Maximum bytes to read. Default 65536.")
                }
                putJsonObject("as_root") {
                    put("type", "boolean")
                    put("description", "Read via root shell. Default false.")
                }
            },
        ),
    )

    // ── write_file ────────────────────────────────────────────────────────

    private val writeFile = ToolDefinitionDto(
        name = "write_file",
        description = """
            Write or append content to a file. Requires user confirmation.
            Creates parent directories automatically. Use `as_root` for /system writes.
        """.trimIndent(),
        inputSchema = schema(
            required = listOf("path", "content"),
            properties = buildJsonObject {
                putJsonObject("path") {
                    put("type", "string")
                    put("description", "Absolute path to write")
                }
                putJsonObject("content") {
                    put("type", "string")
                    put("description", "Text content to write")
                }
                putJsonObject("append") {
                    put("type", "boolean")
                    put("description", "Append instead of overwrite. Default false.")
                }
                putJsonObject("as_root") {
                    put("type", "boolean")
                    put("description", "Write via root shell. Default false.")
                }
            },
        ),
    )

    // ── list_directory ────────────────────────────────────────────────────

    private val listDirectory = ToolDefinitionDto(
        name = "list_directory",
        description = "List files and directories at a given path. Optionally recurse up to 3 levels deep.",
        inputSchema = schema(
            required = listOf("path"),
            properties = buildJsonObject {
                putJsonObject("path") {
                    put("type", "string")
                    put("description", "Absolute directory path to list")
                }
                putJsonObject("recursive") {
                    put("type", "boolean")
                    put("description", "List subdirectories recursively (max depth 3). Default false.")
                }
                putJsonObject("as_root") {
                    put("type", "boolean")
                    put("description", "List via root shell. Default false.")
                }
            },
        ),
    )

    // ── get_system_info ───────────────────────────────────────────────────

    private val getSystemInfo = ToolDefinitionDto(
        name = "get_system_info",
        description = """
            Get a snapshot of system metrics: CPU stat line, RAM (total/available),
            battery percentage and charging state, uptime, device model, Android version,
            CPU architecture, and whether root is available.
        """.trimIndent(),
        inputSchema = schema(emptyList(), buildJsonObject {}),
    )

    // ── list_processes ────────────────────────────────────────────────────

    private val listProcesses = ToolDefinitionDto(
        name = "list_processes",
        description = "List running processes. Returns PID, PPID, user, RSS, CPU%, and process name.",
        inputSchema = schema(
            required = emptyList(),
            properties = buildJsonObject {
                putJsonObject("limit") {
                    put("type", "integer")
                    put("description", "Max processes to return. Default 30.")
                }
                putJsonObject("sort_by") {
                    put("type", "string")
                    put("description", "Sort order: 'cpu' or 'mem'. Default 'cpu'.")
                }
            },
        ),
    )

    // ── kill_process ──────────────────────────────────────────────────────

    private val killProcess = ToolDefinitionDto(
        name = "kill_process",
        description = """
            Send a signal to a process by PID. Requires user confirmation.
            Root is required to kill system processes. Will never kill the JARVIS process itself.
        """.trimIndent(),
        inputSchema = schema(
            required = listOf("pid"),
            properties = buildJsonObject {
                putJsonObject("pid") {
                    put("type", "integer")
                    put("description", "Process ID to signal")
                }
                putJsonObject("signal") {
                    put("type", "string")
                    put("description", "Signal name: SIGTERM (default) or SIGKILL")
                }
            },
        ),
    )

    // ── list_installed_apps ───────────────────────────────────────────────

    private val listInstalledApps = ToolDefinitionDto(
        name = "list_installed_apps",
        description = "List installed applications. Returns package name, display label, and system/user flag.",
        inputSchema = schema(
            required = emptyList(),
            properties = buildJsonObject {
                putJsonObject("user_only") {
                    put("type", "boolean")
                    put("description", "Exclude system apps. Default true.")
                }
            },
        ),
    )

    // ── get_logcat ────────────────────────────────────────────────────────

    private val getLogcat = ToolDefinitionDto(
        name = "get_logcat",
        description = "Fetch recent Android logcat output. Filter by tag and minimum log level.",
        inputSchema = schema(
            required = emptyList(),
            properties = buildJsonObject {
                putJsonObject("lines") {
                    put("type", "integer")
                    put("description", "Number of recent log lines. Default 100.")
                }
                putJsonObject("tag") {
                    put("type", "string")
                    put("description", "Filter to a specific log tag. Omit for all tags.")
                }
                putJsonObject("level") {
                    put("type", "string")
                    put("description", "Minimum level: V D I W E F. Default V.")
                }
                putJsonObject("as_root") {
                    put("type", "boolean")
                    put("description", "Read kernel/system logs via root. Default false.")
                }
            },
        ),
    )

    // ── network_scan ──────────────────────────────────────────────────────

    private val networkScan = ToolDefinitionDto(
        name = "network_scan",
        description = """
            List nearby WiFi networks visible to the device radio.
            Returns SSID, BSSID, RSSI (dBm), frequency (MHz), and capability flags.
            Requires ACCESS_FINE_LOCATION or NEARBY_WIFI_DEVICES permission.
        """.trimIndent(),
        inputSchema = schema(emptyList(), buildJsonObject {}),
    )

    // ── get_sensors ───────────────────────────────────────────────────────

    private val getSensors = ToolDefinitionDto(
        name = "get_sensors",
        description = "List all hardware sensors available on the device: name, type, vendor, max range, and power draw.",
        inputSchema = schema(emptyList(), buildJsonObject {}),
    )

    // ── terminal_create ───────────────────────────────────────────────────

    private val terminalCreate = ToolDefinitionDto(
        name = "terminal_create",
        description = """
            Open a new PTY terminal session (pseudo-terminal running /system/bin/sh).
            Returns a session_id used by terminal_write and terminal_kill.
            Up to 8 concurrent sessions are supported.
        """.trimIndent(),
        inputSchema = schema(
            required = emptyList(),
            properties = buildJsonObject {
                putJsonObject("name") {
                    put("type", "string")
                    put("description", "Display name for the tab bar. Default 'sh'.")
                }
                putJsonObject("as_root") {
                    put("type", "boolean")
                    put("description", "Send 'su\\n' into the shell on startup. Default false.")
                }
                putJsonObject("rows") {
                    put("type", "integer")
                    put("description", "Terminal height in rows. Default 24.")
                }
                putJsonObject("cols") {
                    put("type", "integer")
                    put("description", "Terminal width in columns. Default 80.")
                }
            },
        ),
    )

    // ── terminal_write ────────────────────────────────────────────────────

    private val terminalWrite = ToolDefinitionDto(
        name = "terminal_write",
        description = """
            Send text (keystrokes) into an open PTY session identified by session_id.
            Include '\\n' to execute a command. Use ANSI escape sequences for special keys.
        """.trimIndent(),
        inputSchema = schema(
            required = listOf("session_id", "text"),
            properties = buildJsonObject {
                putJsonObject("session_id") {
                    put("type", "string")
                    put("description", "Session ID returned by terminal_create")
                }
                putJsonObject("text") {
                    put("type", "string")
                    put("description", "Text to write into the terminal (UTF-8)")
                }
            },
        ),
    )

    // ── terminal_kill ─────────────────────────────────────────────────────

    private val terminalKill = ToolDefinitionDto(
        name = "terminal_kill",
        description = "Close a PTY terminal session and release its file descriptor.",
        inputSchema = schema(
            required = listOf("session_id"),
            properties = buildJsonObject {
                putJsonObject("session_id") {
                    put("type", "string")
                    put("description", "Session ID to kill")
                }
            },
        ),
    )

    // ── set_clipboard ─────────────────────────────────────────────────────

    private val setClipboard = ToolDefinitionDto(
        name = "set_clipboard",
        description = "Write a string to the device system clipboard.",
        inputSchema = schema(
            required = listOf("text"),
            properties = buildJsonObject {
                putJsonObject("text") {
                    put("type", "string")
                    put("description", "Text to place on the clipboard")
                }
                putJsonObject("label") {
                    put("type", "string")
                    put("description", "Clipboard label. Default 'JARVIS'.")
                }
            },
        ),
    )

    // ── get_clipboard ─────────────────────────────────────────────────────

    private val getClipboard = ToolDefinitionDto(
        name = "get_clipboard",
        description = "Read the current text content of the device clipboard.",
        inputSchema = schema(emptyList(), buildJsonObject {}),
    )

    // ── Schema builder helper ─────────────────────────────────────────────

    private fun schema(required: List<String>, properties: JsonObject): JsonObject =
        buildJsonObject {
            put("type", "object")
            put("properties", properties)
            if (required.isNotEmpty()) {
                putJsonArray("required") { required.forEach { add(kotlinx.serialization.json.JsonPrimitive(it)) } }
            }
        }
}
