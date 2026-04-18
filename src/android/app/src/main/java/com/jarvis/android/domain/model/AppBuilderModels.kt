package com.jarvis.android.domain.model

/**
 * A user-created app project managed by the JARVIS App Builder.
 *
 * ## Supported types
 *
 *   [AppType.WEBVIEW]  — Self-contained HTML/CSS/JS bundle, rendered in an
 *                        embedded [WebView]. Runs immediately without installation.
 *   [AppType.SHELL]    — Bash script executed in a JARVIS PTY session.
 *   [AppType.PYTHON]   — Python 3 script; requires Termux or a bundled interpreter.
 *
 * ## Lifecycle
 *
 *   Created → [BuildStatus.IDLE]
 *       │
 *       ▼  user taps Generate
 *   [BuildStatus.GENERATING]   ← AI is writing the source code
 *       │
 *       ▼  code accepted
 *   [BuildStatus.BUILDING]     ← engine validates and packages
 *       │
 *       ├─ success ──► [BuildStatus.READY]     outputPath points to the built artefact
 *       └─ failure ──► [BuildStatus.FAILED]    errorMessage explains the problem
 */
data class AppProject(
    /** Stable UUID-based identifier. */
    val id:           String,

    /** User-visible name. */
    val name:         String,

    /** One-sentence description of what the app does. */
    val description:  String,

    /** Which runtime this app targets. */
    val type:         AppType,

    /** ID of the template used as the starting point, or null for scratch. */
    val templateId:   String?,

    /** The full generated source code (HTML / bash / Python). */
    val sourceCode:   String,

    /** Current build lifecycle state. */
    val buildStatus:  BuildStatus = BuildStatus.IDLE,

    /** Absolute path to the built output file, or null if not yet built. */
    val outputPath:   String? = null,

    /** Human-readable error from the last failed build. */
    val errorMessage: String? = null,

    /** Unix millis when the project was first created. */
    val createdAt:    Long = System.currentTimeMillis(),

    /** Unix millis when the project was last modified. */
    val updatedAt:    Long = System.currentTimeMillis(),
)

// ── App type ──────────────────────────────────────────────────────────────────

enum class AppType(
    val label:       String,
    val extension:   String,
    val description: String,
) {
    WEBVIEW(
        label       = "Web App",
        extension   = "html",
        description = "HTML/CSS/JS app rendered in a full-screen WebView",
    ),
    SHELL(
        label       = "Shell Script",
        extension   = "sh",
        description = "Bash script executed in the JARVIS terminal",
    ),
    PYTHON(
        label       = "Python Script",
        extension   = "py",
        description = "Python 3 script; requires Termux or on-device interpreter",
    ),
}

// ── Build status ──────────────────────────────────────────────────────────────

enum class BuildStatus(val label: String) {
    IDLE("Idle"),
    GENERATING("Generating…"),
    BUILDING("Building…"),
    READY("Ready"),
    FAILED("Failed"),
}

// ── Build result ──────────────────────────────────────────────────────────────

/**
 * Returned by [com.jarvis.android.system.builder.AppBuildEngine.build].
 */
data class BuildResult(
    val projectId:   String,
    val success:     Boolean,
    val outputPath:  String? = null,
    val sizeBytes:   Long    = 0L,
    val errorMessage: String? = null,
    val builtAt:     Long    = System.currentTimeMillis(),
)

// ── App template ──────────────────────────────────────────────────────────────

/**
 * A starter template the user can customise or use as-is.
 *
 * Templates ship with working code so the user gets immediate results
 * without waiting for AI generation.
 */
data class AppTemplate(
    val id:          String,
    val name:        String,
    val description: String,
    val category:    TemplateCategory,
    val type:        AppType,
    val sourceCode:  String,
    val tags:        List<String> = emptyList(),
)

enum class TemplateCategory(val label: String) {
    UTILITY("Utility"),
    SYSTEM("System"),
    PRODUCTIVITY("Productivity"),
    MEDIA("Media"),
    DEVELOPER("Developer"),
    GAME("Game"),
}

// ── Generation request ────────────────────────────────────────────────────────

/**
 * Input to [com.jarvis.android.system.builder.AppCodeGenerator.generate].
 */
data class GenerationRequest(
    val projectName:  String,
    val description:  String,
    val type:         AppType,
    val templateBase: String? = null,  // existing code to build on, null = from scratch
    val extraHints:   String  = "",    // user-specified constraints / style preferences
)
