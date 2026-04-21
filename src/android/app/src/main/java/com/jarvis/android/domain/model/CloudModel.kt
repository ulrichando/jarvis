package com.jarvis.android.domain.model

/**
 * Provider of a cloud LLM. Each provider has a separate API key and base URL;
 * the [CloudModel] picker in the home top bar surfaces models from a provider
 * only when that provider's key is configured.
 *
 * Adding a new provider is a four-step edit:
 *   1. Add an enum entry here with its display name.
 *   2. Extend `assets/endpoints.json` (or use the existing env) with its URL.
 *   3. Teach `ApiKeyProvider` to store and retrieve its key.
 *   4. Append its models to [CloudModel.CATALOG].
 */
enum class CloudProvider(val displayName: String) {
    ANTHROPIC("Anthropic"),
    OPENAI   ("OpenAI"),
    DEEPSEEK ("DeepSeek"),
    GROQ     ("Groq"),
    GOOGLE   ("Google"),
    XAI      ("xAI"),
    OPENROUTER("OpenRouter"),
    MISTRAL  ("Mistral"),
}

/**
 * A cloud-hosted LLM offered to the user. Mirrors [ModelEntry] but for API-
 * backed backends — surfaced in the home top-bar picker whenever its
 * [provider]'s key is set.
 *
 * `id` is the provider-native model slug used in the outbound request body.
 * `label` is the short user-facing name.
 * `description` is the one-line justification shown under the label.
 */
data class CloudModel(
    val provider:    CloudProvider,
    val id:          String,
    val label:       String,
    val description: String,
) {
    companion object {

        /** The default Claude model used until the user picks one explicitly. */
        const val DEFAULT_ANTHROPIC_ID = "claude-sonnet-4-6"

        /**
         * Catalog of cloud models surfaced in the home-bar picker. Populated
         * from the same source of truth as each provider's model page so we
         * don't ship slugs that have been retired.
         */
        val CATALOG: List<CloudModel> = listOf(
            // ── Anthropic ────────────────────────────────────────────────
            CloudModel(CloudProvider.ANTHROPIC, "claude-opus-4-6",
                "Opus 4.6",        "Most capable Claude — best for complex reasoning."),
            CloudModel(CloudProvider.ANTHROPIC, "claude-sonnet-4-6",
                "Sonnet 4.6",      "Best balance of speed and intelligence."),
            CloudModel(CloudProvider.ANTHROPIC, "claude-haiku-4-5-20251001",
                "Haiku 4.5",       "Fastest Claude — low-latency queries + tools."),

            // ── OpenAI ───────────────────────────────────────────────────
            CloudModel(CloudProvider.OPENAI,    "gpt-5",
                "GPT-5",           "OpenAI's flagship reasoning model."),
            CloudModel(CloudProvider.OPENAI,    "gpt-5-mini",
                "GPT-5 Mini",      "Cheaper GPT-5 with most of the capability."),
            CloudModel(CloudProvider.OPENAI,    "o3",
                "o3",              "Deep-thinking reasoning chain; slow but strong."),

            // ── DeepSeek ─────────────────────────────────────────────────
            CloudModel(CloudProvider.DEEPSEEK,  "deepseek-chat",
                "DeepSeek V3",     "General chat + coding; sharp, fast, cheap."),
            CloudModel(CloudProvider.DEEPSEEK,  "deepseek-reasoner",
                "DeepSeek R1",     "DeepSeek's R1 reasoner — explicit step-by-step chains."),

            // ── Groq (hosts others' weights on LPU hardware) ─────────────
            CloudModel(CloudProvider.GROQ,      "openai/gpt-oss-120b",
                "GPT-OSS 120B",    "OpenAI's open weights on Groq LPUs — very fast."),
            CloudModel(CloudProvider.GROQ,      "llama-3.3-70b-versatile",
                "Llama 3.3 70B",   "Meta's 70B on Groq — low-latency general chat."),

            // ── Google Gemini ────────────────────────────────────────────
            CloudModel(CloudProvider.GOOGLE,    "gemini-2.5-pro",
                "Gemini 2.5 Pro",  "Google's flagship — strong multimodal + long context."),
            CloudModel(CloudProvider.GOOGLE,    "gemini-2.5-flash",
                "Gemini 2.5 Flash","Fast, cheap Google model; great for tools."),
            CloudModel(CloudProvider.GOOGLE,    "gemini-2.5-flash-preview-native-audio-dialog",
                "Gemini 2.5 Native Audio",
                "Native audio in/out — speak to and hear the model directly."),

            // ── xAI Grok ─────────────────────────────────────────────────
            CloudModel(CloudProvider.XAI,       "grok-4",
                "Grok 4",          "xAI's latest — real-time web knowledge baked in."),

            // ── OpenRouter (meta-provider routing many of the above) ─────
            CloudModel(CloudProvider.OPENROUTER,"openrouter/auto",
                "OpenRouter Auto", "OpenRouter picks the best model for each query."),

            // ── Mistral ──────────────────────────────────────────────────
            CloudModel(CloudProvider.MISTRAL,   "mistral-large-latest",
                "Mistral Large",   "Mistral's flagship chat + reasoning model."),
        )
    }
}
