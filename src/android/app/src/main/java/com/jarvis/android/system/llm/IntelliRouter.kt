package com.jarvis.android.system.llm

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.util.Log
import com.jarvis.android.domain.model.DownloadState
import com.jarvis.android.domain.model.ModelCapability
import com.jarvis.android.domain.model.ModelEntry
import com.jarvis.android.domain.model.RoutingMode
import com.jarvis.android.domain.repository.ModelRepository
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.first
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Routing decision engine for JARVIS on-device ↔ cloud inference.
 *
 * Given a user query and the current [RoutingMode], [IntelliRouter.route] returns
 * a [RoutingDecision] telling the caller which backend to use and, when going
 * local, which specific model ID to load.
 *
 * ## Decision tree (AUTO mode)
 *
 * ```
 * AUTO
 *  ├─ Network unavailable?          → LOCAL  (offline fallback)
 *  ├─ Image attached?               → CLOUD  (vision best on Claude)
 *  ├─ Privacy-sensitive query?      → LOCAL  (never send to cloud)
 *  ├─ Code query + code model?      → LOCAL  (prefer specialised model)
 *  ├─ Short/simple query + model?   → LOCAL  (save API quota)
 *  └─ Default                       → CLOUD  (best quality)
 * ```
 *
 * In HYBRID mode the decision always returns [Backend.HYBRID] so the caller
 * can run a local draft first, then refine with Claude.
 *
 * ## Model selection (LOCAL path)
 *
 * If the already-loaded model satisfies the query type it is reused to avoid
 * a costly reload. Otherwise [selectBestModel] picks the highest-scoring
 * downloaded model using a weighted heuristic:
 *   - Capability match  (code query → CODE capability model gets +30 pts)
 *   - Parameter count   (larger = higher quality, capped at 8B)
 *   - RAM overhead      (smaller RAM req = +5 pts, avoids OOM on constrained devices)
 */
@Singleton
class IntelliRouter @Inject constructor(
    @ApplicationContext private val context: Context,
    private val modelRepository: ModelRepository,
) {

    // ── Public API ────────────────────────────────────────────────────────────

    /**
     * Compute the routing decision for a single turn.
     *
     * @param query      The raw user message text.
     * @param hasImage   True if the turn includes an image attachment.
     * @return A [RoutingDecision] the caller should execute.
     */
    suspend fun route(
        query:    String,
        hasImage: Boolean = false,
    ): RoutingDecision {
        val mode          = modelRepository.observeRoutingMode().value
        val loadedModelId = modelRepository.observeLoadedModelId().value
        val downloaded    = modelRepository.observeDownloaded().first()

        return when (mode) {
            RoutingMode.LOCAL  -> routeLocal(query, loadedModelId, downloaded)
            RoutingMode.CLOUD  -> RoutingDecision(Backend.CLOUD, reason = "RoutingMode=CLOUD")
            RoutingMode.HYBRID -> routeHybrid(loadedModelId, downloaded)
            RoutingMode.AUTO   -> routeAuto(query, hasImage, loadedModelId, downloaded)
        }.also { decision ->
            Log.d(TAG, "route(mode=$mode, loaded=$loadedModelId) → $decision")
        }
    }

    /**
     * Observe the [RoutingMode] StateFlow directly (for UI binding).
     * Changes are reflected immediately without going through [route].
     */
    fun observeRoutingMode(): StateFlow<RoutingMode> =
        modelRepository.observeRoutingMode()

    // ── Routing strategies ────────────────────────────────────────────────────

    private fun routeAuto(
        query:        String,
        hasImage:     Boolean,
        loadedId:     String?,
        downloaded:   List<ModelEntry>,
    ): RoutingDecision {

        // 1. Offline → must use local (or fail gracefully)
        if (!isNetworkAvailable()) {
            val modelId = loadedId ?: selectBestModel(query, downloaded)?.id
            return if (modelId != null)
                RoutingDecision(Backend.LOCAL, modelId, "offline — local only")
            else
                RoutingDecision(Backend.CLOUD, reason = "offline but no local model loaded")
        }

        // 2. Image attached → cloud (Claude vision > local vision quality)
        if (hasImage) {
            return RoutingDecision(Backend.CLOUD, reason = "image input → cloud vision")
        }

        // 3. Privacy-sensitive → never leave the device
        if (isPrivacySensitive(query)) {
            val modelId = loadedId ?: selectBestModel(query, downloaded)?.id
            return if (modelId != null)
                RoutingDecision(Backend.LOCAL, modelId, "privacy-sensitive query → local")
            else
                RoutingDecision(Backend.CLOUD, reason = "privacy-sensitive but no model available")
        }

        // 4. Code query + code model available → stay local
        if (isCodeQuery(query) && downloaded.isNotEmpty()) {
            val codeModel = selectBestModel(query, downloaded, preferCapability = ModelCapability.CODE)
            if (codeModel != null) {
                return RoutingDecision(
                    backend     = Backend.LOCAL,
                    localModelId = loadedId?.takeIf { it == codeModel.id } ?: codeModel.id,
                    reason      = "code query → local code model (${codeModel.name})",
                )
            }
        }

        // 5. Short / simple query + local model already loaded → reuse
        if (isSimpleQuery(query) && loadedId != null) {
            return RoutingDecision(Backend.LOCAL, loadedId, "simple query + model loaded → local")
        }

        // 6. Default → cloud (complex / creative / long queries)
        return RoutingDecision(Backend.CLOUD, reason = "default AUTO → cloud")
    }

    private fun routeLocal(
        query:      String,
        loadedId:   String?,
        downloaded: List<ModelEntry>,
    ): RoutingDecision {
        val modelId = loadedId
            ?: selectBestModel(query, downloaded)?.id
            ?: return RoutingDecision(
                Backend.CLOUD,
                reason = "RoutingMode=LOCAL but no model downloaded — falling back to cloud",
            )
        return RoutingDecision(Backend.LOCAL, modelId, "RoutingMode=LOCAL")
    }

    private fun routeHybrid(
        loadedId:   String?,
        downloaded: List<ModelEntry>,
    ): RoutingDecision {
        val modelId = loadedId ?: downloaded.firstOrNull()?.id
        return RoutingDecision(
            backend      = Backend.HYBRID,
            localModelId = modelId,
            reason       = "RoutingMode=HYBRID — local draft → cloud refinement",
        )
    }

    // ── Model selection ───────────────────────────────────────────────────────

    /**
     * Pick the best downloaded model for [query], optionally biasing toward
     * [preferCapability].
     *
     * Scoring (higher = better):
     *   +30  capability match (if [preferCapability] is set)
     *   +10  per billion parameters (capped at 8B → +80 max)
     *   + 5  if ramRequiredMb ≤ 4096 (avoids picking a 6 GB model on a 6 GB device)
     *   + 5  if already loaded (avoids reload cost)
     */
    private fun selectBestModel(
        query:             String,
        candidates:        List<ModelEntry>,
        preferCapability:  ModelCapability? = null,
    ): ModelEntry? {
        if (candidates.isEmpty()) return null
        val loadedId = modelRepository.observeLoadedModelId().value
        val cap = preferCapability ?: if (isCodeQuery(query)) ModelCapability.CODE else null

        return candidates
            .filter { it.downloadState is DownloadState.Downloaded }
            .maxByOrNull { model ->
                var score = 0

                // Capability bonus
                if (cap != null && model.capabilities.contains(cap)) score += 30

                // Param count (parse "1B", "3.8B", "7B" etc.)
                val billions = parseParamBillions(model.paramCount)
                score += (billions.coerceAtMost(8.0) * 10).toInt()

                // RAM heuristic
                if (model.ramRequiredMb <= 4_096) score += 5

                // Avoid reload cost
                if (model.id == loadedId) score += 5

                score
            }
    }

    // ── Query classifiers ─────────────────────────────────────────────────────

    /** True when the query likely involves programming or shell commands. */
    private fun isCodeQuery(query: String): Boolean {
        val lower = query.lowercase()
        return CODE_KEYWORDS.any { lower.contains(it) } ||
                CODE_PATTERNS.any { it.containsMatchIn(query) }
    }

    /** True when the query may contain PII or sensitive credentials. */
    private fun isPrivacySensitive(query: String): Boolean {
        val lower = query.lowercase()
        return PRIVACY_KEYWORDS.any { lower.contains(it) }
    }

    /** True for short, factual queries that a local model can handle cheaply. */
    private fun isSimpleQuery(query: String): Boolean =
        query.length < SIMPLE_QUERY_MAX_CHARS &&
                query.count { it == '?' } <= 1 &&
                COMPLEXITY_KEYWORDS.none { query.lowercase().contains(it) }

    // ── Connectivity ──────────────────────────────────────────────────────────

    private fun isNetworkAvailable(): Boolean {
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val network = cm.activeNetwork ?: return false
        val caps = cm.getNetworkCapabilities(network) ?: return false
        return caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) &&
                caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    /** Parse "1B", "3.8B", "0.5B", "7B" → Double. Returns 0.0 on parse failure. */
    private fun parseParamBillions(paramCount: String): Double =
        PARAM_REGEX.find(paramCount)?.groupValues?.getOrNull(1)?.toDoubleOrNull() ?: 0.0

    // ── Constants ─────────────────────────────────────────────────────────────

    companion object {
        private const val TAG = "IntelliRouter"

        private const val SIMPLE_QUERY_MAX_CHARS = 200

        private val CODE_KEYWORDS = setOf(
            "code", "function", "class", "script", "debug", "error", "exception",
            "python", "kotlin", "java", "javascript", "typescript", "rust", "go",
            "bash", "shell", "sql", "html", "css", "regex", "algorithm", "refactor",
            "implement", "compile", "runtime", "syntax", "variable", "loop", "array",
            "bug", "fix the", "write a", "create a", "generate a", "parse",
        )

        private val CODE_PATTERNS = listOf(
            Regex("""```"""),                              // fenced code block
            Regex("""\b(def|fun|func|void|int|str|val|var|let|const)\b"""),
            Regex("""[{};]\s*$""", RegexOption.MULTILINE), // C-style braces
            Regex("""\$\{"""),                             // shell/template interpolation
            Regex("""^\s*(import|from|#include|using)\s""", RegexOption.MULTILINE),
        )

        private val PRIVACY_KEYWORDS = setOf(
            "password", "passwd", "secret", "api key", "private key", "token",
            "credential", "ssn", "social security", "credit card", "bank account",
            "passport", "license plate", "home address", "phone number",
            "medical", "health record", "personal", "confidential",
        )

        private val COMPLEXITY_KEYWORDS = setOf(
            "explain in detail", "write a comprehensive", "analyze", "compare",
            "pros and cons", "step by step", "research", "essay", "report",
            "creative", "story", "poem", "brainstorm", "strategy",
        )

        private val PARAM_REGEX = Regex("""([\d.]+)[Bb]""")
    }
}

// ── Routing result types ──────────────────────────────────────────────────────

/**
 * The resolved routing decision for a single chat turn.
 *
 * @param backend      Which inference backend to use.
 * @param localModelId If [Backend.LOCAL] or [Backend.HYBRID], the model ID to
 *                     load (or reuse if already loaded). Null for [Backend.CLOUD].
 * @param reason       Human-readable explanation, logged for debugging.
 */
data class RoutingDecision(
    val backend:      Backend,
    val localModelId: String? = null,
    val reason:       String  = "",
)

/**
 * The three execution paths [IntelliRouter] can select.
 *
 *   [LOCAL]  — Run the query through [LlamaJNI] / [MediaPipeLLM] / [OllamaBridge].
 *   [CLOUD]  — Send the query to the Claude API.
 *   [HYBRID] — Run a local draft turn, then feed the draft to Claude for refinement.
 */
enum class Backend {
    LOCAL,
    CLOUD,
    HYBRID,
}
