package com.jarvis.android.system.builder

import android.util.Log
import com.jarvis.android.domain.model.AppType
import com.jarvis.android.domain.model.GenerationRequest
import com.jarvis.android.system.llm.GenerationConfig
import com.jarvis.android.system.llm.IntelliRouter
import com.jarvis.android.domain.repository.ModelRepository
import com.jarvis.android.system.llm.Backend
import kotlinx.coroutines.flow.toList
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Generates app source code from a natural-language [GenerationRequest].
 *
 * ## Backend selection
 *
 * [IntelliRouter] decides whether to use a local model or cloud:
 *   - **LOCAL / HYBRID** — calls the loaded [ModelRepository] model directly.
 *     Code generation prompts are long and structured, so a 7B+ code model
 *     is preferred (Qwen 2.5 Coder, DeepSeek Coder).
 *   - **CLOUD** — returns a ready-to-paste prompt so the user can run it in the
 *     Chat tab (the App Builder does not have direct access to the Claude API).
 *
 * ## Output contract
 *
 * [generate] returns a [GenerationResult]:
 *   - [GenerationResult.Code] with the raw source string on success
 *   - [GenerationResult.PromptForCloud] with a clipboard-ready prompt string
 *     when CLOUD routing is selected but no API is wired into this path
 *   - [GenerationResult.Error] on model/inference failure
 *
 * The caller ([AppBuildEngine]) is responsible for extracting and validating
 * the code block from the result.
 */
@Singleton
class AppCodeGenerator @Inject constructor(
    private val intelliRouter:   IntelliRouter,
    private val modelRepository: ModelRepository,
) {

    suspend fun generate(request: GenerationRequest): GenerationResult {
        val decision = intelliRouter.route(
            query    = request.description,
            hasImage = false,
        )
        Log.i(TAG, "Generation routing: ${decision.backend} — model=${decision.localModelId}")

        return when (decision.backend) {
            Backend.LOCAL, Backend.HYBRID -> generateLocal(request, decision.localModelId)
            Backend.CLOUD                 -> GenerationResult.PromptForCloud(buildCloudPrompt(request))
        }
    }

    // ── Local generation ──────────────────────────────────────────────────────

    private suspend fun generateLocal(
        request: GenerationRequest,
        modelId: String?,
    ): GenerationResult {
        val id = modelId ?: modelRepository.observeLoadedModelId().value
            ?: return GenerationResult.Error(
                "No local model loaded. Load a model in the Local AI tab first, " +
                "or switch routing mode to CLOUD."
            )

        // Ensure the model is loaded before generating
        val loaded = modelRepository.observeLoadedModelId().value
        if (loaded != id) {
            try {
                modelRepository.loadModel(id).toList()
            } catch (e: Exception) {
                return GenerationResult.Error("Failed to load model $id: ${e.message}")
            }
        }

        val prompt = buildLocalPrompt(request)
        val config = GenerationConfig(
            maxNewTokens = LOCAL_MAX_TOKENS,
            temperature  = 0.2f,   // low temperature for deterministic code
            topK         = 20,
        )

        return try {
            val sb = StringBuilder()
            modelRepository.generate(id, prompt, config).collect { token -> sb.append(token) }
            val raw = sb.toString()
            val code = extractCodeBlock(raw, request.type) ?: raw.trim()
            if (code.isBlank()) {
                GenerationResult.Error("Model returned empty output — try a more detailed description.")
            } else {
                GenerationResult.Code(code)
            }
        } catch (e: Exception) {
            Log.e(TAG, "local generation failed", e)
            GenerationResult.Error(e.message ?: "Inference failed")
        }
    }

    // ── Prompt builders ───────────────────────────────────────────────────────

    private fun buildLocalPrompt(request: GenerationRequest): String {
        val typeGuide = when (request.type) {
            AppType.WEBVIEW ->
                "Write a complete, self-contained HTML5 file (single file, no external imports). " +
                "Include all CSS in a <style> tag and all JS in a <script> tag. " +
                "Use a dark theme with colors: background #0A0A0A, surface #141414, " +
                "gold accent #C9A84C, text #F0EDE8. Make it mobile-friendly."
            AppType.SHELL   ->
                "Write a POSIX-compatible shell script (#!/system/bin/sh). " +
                "Include error handling and ANSI colour output (gold=\\033[38;5;178m). " +
                "The script runs on a rooted Android device."
            AppType.PYTHON  ->
                "Write a Python 3 script. Include a shebang #!/usr/bin/env python3. " +
                "Keep imports minimal. The script runs on Android via Termux."
        }

        val baseSection = if (!request.templateBase.isNullOrBlank()) {
            "\n\nStarting template to modify (keep the same structure/style):\n```\n${request.templateBase.take(2000)}\n```"
        } else ""

        val hintsSection = if (request.extraHints.isNotBlank()) {
            "\n\nAdditional requirements: ${request.extraHints}"
        } else ""

        return """You are an expert mobile app developer. Generate working code for the following app.

App name: ${request.projectName}
Description: ${request.description}

Instructions: $typeGuide$baseSection$hintsSection

Return ONLY the complete source code inside a single fenced code block. No explanations before or after.

```${request.type.extension}
""".trimIndent()
    }

    private fun buildCloudPrompt(request: GenerationRequest): String {
        val typeGuide = when (request.type) {
            AppType.WEBVIEW ->
                "a complete single-file HTML5 app (CSS in <style>, JS in <script>). " +
                "Dark theme: background #0A0A0A, surface #141414, gold accent #C9A84C. Mobile-friendly."
            AppType.SHELL   ->
                "a POSIX shell script (#!/system/bin/sh) for rooted Android with ANSI colours."
            AppType.PYTHON  ->
                "a Python 3 script (#!/usr/bin/env python3) for Android/Termux."
        }

        return buildString {
            appendLine("Generate ${typeGuide}")
            appendLine()
            appendLine("**App name:** ${request.projectName}")
            appendLine("**Description:** ${request.description}")
            if (request.extraHints.isNotBlank()) {
                appendLine("**Extra requirements:** ${request.extraHints}")
            }
            if (!request.templateBase.isNullOrBlank()) {
                appendLine()
                appendLine("Base this on the following starter template:")
                appendLine("```")
                appendLine(request.templateBase.take(1500))
                appendLine("```")
            }
            appendLine()
            appendLine("Return only the complete source code in a fenced code block.")
        }
    }

    // ── Code extraction ───────────────────────────────────────────────────────

    /**
     * Extract the first fenced code block from [raw].
     * Falls back to returning [raw] directly if no fence is found.
     */
    private fun extractCodeBlock(raw: String, type: AppType): String? {
        // Try type-specific fence first (```html, ```sh, ```python)
        val typeFence = Regex("```(?:${type.extension}|${type.name.lowercase()})\\s*\\n([\\s\\S]*?)\\n?```", RegexOption.IGNORE_CASE)
        typeFence.find(raw)?.groupValues?.getOrNull(1)?.let { return it.trim() }

        // Generic fence
        val generic = Regex("```[^\\n]*\\n([\\s\\S]*?)\\n?```")
        generic.find(raw)?.groupValues?.getOrNull(1)?.let { return it.trim() }

        return null
    }

    companion object {
        private const val TAG             = "AppCodeGenerator"
        private const val LOCAL_MAX_TOKENS = 2048
    }
}

// ── Result types ──────────────────────────────────────────────────────────────

sealed class GenerationResult {
    /** Successfully generated source code. */
    data class Code(val sourceCode: String) : GenerationResult()

    /**
     * CLOUD routing selected — the builder can't call Claude API directly.
     * [prompt] is a ready-to-paste message for the Chat tab.
     */
    data class PromptForCloud(val prompt: String) : GenerationResult()

    /** Inference or model load failed. */
    data class Error(val message: String) : GenerationResult()
}
