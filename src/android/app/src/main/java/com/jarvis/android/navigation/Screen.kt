package com.jarvis.android.navigation

/**
 * Type-safe route definitions for the JARVIS NavGraph.
 *
 * All routes are plain strings — Compose Navigation 2.x does not require
 * serializable objects. Complex arguments (conversationId) are injected as
 * path segments so the back stack handles them correctly.
 */
sealed class Screen(val route: String) {

    // ── Entry points ──────────────────────────────────────────────────────────

    /** Shown once on first launch; writes `onboarding_done` pref on exit. */
    data object Onboarding : Screen("onboarding")

    // ── Main screens ──────────────────────────────────────────────────────────

    /** Claude AI chat with conversation drawer. */
    data object Chat : Screen("chat/{conversationId}") {
        const val ARG_CONVERSATION_ID = "conversationId"
        fun route(conversationId: String = "default") = "chat/$conversationId"
    }

    /** Full-screen PTY terminal with session tabs. */
    data object Terminal : Screen("terminal")

    /** File manager with breadcrumb navigation and optional root mode. */
    data object FileManager : Screen("file_manager")

    /** System dashboard — overview, processes, apps, logcat. */
    data object SystemDashboard : Screen("system_dashboard")

    /** WiFi scanner and network info. */
    data object Network : Screen("network")

    /** Live sensor grid + location + orientation tabs. */
    data object SensorDashboard : Screen("sensor_dashboard")

    /** Three-tier permission matrix (Dangerous / Special / Root). */
    data object Permissions : Screen("permissions")

    /** API key + endpoint settings. */
    data object Settings : Screen("settings")

    /** Magisk / KernelSU auto-download installer. */
    data object RootInstaller : Screen("root_installer")

    // ── Module A ──────────────────────────────────────────────────────────────

    /** Local LLM engine — model catalog, inference, benchmark, settings. */
    data object LocalAi : Screen("local_ai")

    // ── Module B ──────────────────────────────────────────────────────────────

    /** On-device app builder — projects, AI code generation, templates. */
    data object AppBuilder : Screen("app_builder")

    // ── Module C ──────────────────────────────────────────────────────────────

    /** Cybersecurity suite — port scan, HTTP inspect, process/network/logcat. */
    data object CyberSuite : Screen("cyber_suite")
}
