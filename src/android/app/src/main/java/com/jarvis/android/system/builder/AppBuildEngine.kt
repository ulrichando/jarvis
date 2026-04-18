package com.jarvis.android.system.builder

import android.content.Context
import android.util.Log
import com.jarvis.android.domain.model.AppProject
import com.jarvis.android.domain.model.AppType
import com.jarvis.android.domain.model.BuildResult
import dagger.hilt.android.qualifiers.ApplicationContext
import java.io.File
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Validates and packages app source code into runnable output files.
 *
 * ## WebView apps
 *
 * The source code is written as-is to `<filesDir>/apps/<projectId>/index.html`.
 * JARVIS's embedded [AppRunnerActivity] opens this path in a full-screen WebView.
 * No compilation is required — HTML/JS runs directly.
 *
 * ## Shell scripts
 *
 * Written to `<filesDir>/apps/<projectId>/run.sh` with `chmod 755`.
 * Executed via the JARVIS [TerminalSessionManager] when the user taps Run.
 *
 * ## Python scripts
 *
 * Written to `<filesDir>/apps/<projectId>/main.py`.
 * Requires `/usr/bin/python3` (Termux) or the path can be overridden.
 *
 * ## Validation
 *
 * A lightweight syntax check runs before writing:
 *   - WebView: must contain `<html` and `</html>` (or `<!DOCTYPE html`)
 *   - Shell: must start with `#!` and not be empty
 *   - Python: must be non-empty; basic `SyntaxError` check via regex heuristics
 *
 * The build NEVER calls an external compiler — all validation is structural.
 */
@Singleton
class AppBuildEngine @Inject constructor(
    @ApplicationContext private val context: Context,
) {

    /**
     * Validate and write [project.sourceCode] to the output directory.
     *
     * @return [BuildResult] with [BuildResult.outputPath] set on success.
     */
    fun build(project: AppProject): BuildResult {
        Log.i(TAG, "Building project ${project.id} (${project.type})")

        // ── Validate ──────────────────────────────────────────────────────────
        val validationError = validate(project.sourceCode, project.type)
        if (validationError != null) {
            Log.w(TAG, "Validation failed: $validationError")
            return BuildResult(
                projectId    = project.id,
                success      = false,
                errorMessage = validationError,
            )
        }

        // ── Write to storage ──────────────────────────────────────────────────
        return try {
            val dir = projectDir(project.id)
            dir.mkdirs()

            val outputFile = outputFile(dir, project.type)
            outputFile.writeText(project.sourceCode, Charsets.UTF_8)

            if (project.type == AppType.SHELL) {
                outputFile.setExecutable(true, false)
            }

            Log.i(TAG, "Build complete: ${outputFile.absolutePath} (${outputFile.length()} bytes)")
            BuildResult(
                projectId   = project.id,
                success     = true,
                outputPath  = outputFile.absolutePath,
                sizeBytes   = outputFile.length(),
            )
        } catch (e: Exception) {
            Log.e(TAG, "Build write failed", e)
            BuildResult(
                projectId    = project.id,
                success      = false,
                errorMessage = "Write failed: ${e.message}",
            )
        }
    }

    /**
     * Delete all output files for [projectId].
     * Called when the project is deleted.
     */
    fun clean(projectId: String) {
        projectDir(projectId).deleteRecursively()
        Log.i(TAG, "Cleaned project: $projectId")
    }

    /**
     * Return the launch URI for the built output of [project].
     *
     *   WebView  → `file:///data/.../<id>/index.html`
     *   Shell    → absolute path (opened in terminal)
     *   Python   → absolute path (opened in terminal with `python3`)
     */
    fun launchPath(project: AppProject): String? {
        val file = outputFile(projectDir(project.id), project.type)
        return if (file.exists()) file.absolutePath else null
    }

    // ── Validation ────────────────────────────────────────────────────────────

    private fun validate(code: String, type: AppType): String? {
        val trimmed = code.trim()
        if (trimmed.isBlank()) return "Source code is empty"
        if (trimmed.length < MIN_CODE_LENGTH) return "Source code is too short (< $MIN_CODE_LENGTH chars)"

        return when (type) {
            AppType.WEBVIEW -> validateHtml(trimmed)
            AppType.SHELL   -> validateShell(trimmed)
            AppType.PYTHON  -> validatePython(trimmed)
        }
    }

    private fun validateHtml(code: String): String? {
        val lower = code.lowercase()
        if (!lower.contains("<html") && !lower.contains("<!doctype html")) {
            return "WebView app must contain an <html> tag or <!DOCTYPE html> declaration"
        }
        if (!lower.contains("</html>") && !lower.contains("</body>")) {
            return "HTML appears to be incomplete (no closing </html> or </body>)"
        }
        return null
    }

    private fun validateShell(code: String): String? {
        if (!code.startsWith("#!")) {
            return "Shell script must start with a shebang (#!)"
        }
        val firstLine = code.lines().first()
        if (!firstLine.contains("sh") && !firstLine.contains("bash")) {
            return "Shell script shebang should reference sh or bash: $firstLine"
        }
        return null
    }

    private fun validatePython(code: String): String? {
        // Disallow obvious syntax errors: unmatched quotes / brackets
        val opens  = code.count { it == '(' } - code.count { it == ')' }
        val bracks = code.count { it == '[' } - code.count { it == ']' }
        val braces = code.count { it == '{' } - code.count { it == '}' }
        if (opens != 0 || bracks != 0 || braces != 0) {
            return "Python code has unmatched brackets (parens: $opens, brackets: $bracks, braces: $braces)"
        }
        return null
    }

    // ── Path helpers ──────────────────────────────────────────────────────────

    private fun projectDir(projectId: String): File =
        File(context.filesDir, "apps/$projectId")

    private fun outputFile(dir: File, type: AppType): File = when (type) {
        AppType.WEBVIEW -> File(dir, "index.html")
        AppType.SHELL   -> File(dir, "run.sh")
        AppType.PYTHON  -> File(dir, "main.py")
    }

    companion object {
        private const val TAG              = "AppBuildEngine"
        private const val MIN_CODE_LENGTH  = 50
    }
}
