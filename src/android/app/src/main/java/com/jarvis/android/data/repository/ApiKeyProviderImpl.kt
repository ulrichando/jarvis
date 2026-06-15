package com.jarvis.android.data.repository

import android.content.Context
import android.util.Log
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import com.jarvis.android.BuildConfig
import com.jarvis.android.core.network.ApiKeyProvider
import com.jarvis.android.domain.model.CloudProvider
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Stores and retrieves the Anthropic API key from [EncryptedSharedPreferences].
 *
 * The key material is protected by AES-256-GCM (value) and AES-256-SIV (pref key)
 * backed by the Android Keystore. The master key is created with
 * [MasterKey.KeyScheme.AES256_GCM] and is non-exportable.
 *
 * Implements [ApiKeyProvider] so the OkHttp [ApiKeyInterceptor] can inject the
 * key into every request header without holding a direct repository reference.
 *
 * Key lifecycle:
 *   - Saved from the onboarding screen or Settings → API Key
 *   - Read by [ApiKeyInterceptor] on every HTTP request
 *   - Cleared by [clear] when the user signs out or resets the app
 */
@Singleton
class ApiKeyProviderImpl @Inject constructor(
    @ApplicationContext private val context: Context,
) : ApiKeyProvider {

    // Tick whenever ANY key/url/connection changes. Consumers that derive UI
    // state from key presence (e.g. the chat top-bar picker) combine() with
    // this flow so they refresh as soon as the user saves a new key in
    // Settings — no Activity restart needed.
    private val _keyChanges = MutableStateFlow(0L)
    val keyChanges: StateFlow<Long> = _keyChanges.asStateFlow()
    private fun bump() { _keyChanges.value = System.currentTimeMillis() }

    /** Lazy init — EncryptedSharedPreferences creation is blocking I/O. */
    private val prefs by lazy {
        val p = try {
            val masterKey = MasterKey.Builder(context)
                .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                .build()
            EncryptedSharedPreferences.create(
                context,
                PREFS_FILE,
                masterKey,
                EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
            )
        } catch (e: Exception) {
            Log.e(TAG, "EncryptedSharedPreferences init failed — falling back to plaintext: ${e.message}")
            // Fallback keeps the app functional even if Keystore is unavailable (e.g. emulator)
            context.getSharedPreferences(PREFS_FILE + "_fallback", Context.MODE_PRIVATE)
        }
        // Migrate older installs whose default provider is ANTHROPIC (the
        // original out-of-the-box default) to GROQ + gpt-oss-120b, which is
        // the user's daily driver. We run this exactly once per device: a
        // sentinel flag in the same prefs file prevents overwriting any
        // later explicit choice the user makes after migration.
        if (!p.getBoolean(KEY_DEFAULT_MIGRATION, false)) {
            val current = p.getString(KEY_DIRECT_PROVIDER, null)
            val edit = p.edit()
            if (current == null || current == CloudProvider.ANTHROPIC.name) {
                edit.putString(KEY_DIRECT_PROVIDER, CloudProvider.GROQ.name)
                edit.putString("${KEY_DIRECT_MODEL}_groq", "openai/gpt-oss-120b")
                Log.i(TAG, "Default-provider migration: ${current ?: "(unset)"} → GROQ / gpt-oss-120b")
            }
            edit.putBoolean(KEY_DEFAULT_MIGRATION, true).apply()
        }
        p
    }

    // ── ApiKeyProvider ────────────────────────────────────────────────────

    override fun getApiKey(): String = prefs.getString(KEY_API_KEY, "") ?: ""

    override fun setApiKey(key: String) {
        prefs.edit().putString(KEY_API_KEY, key.trim()).apply()
        Log.i(TAG, "API key saved (${key.take(6)}…)")
        bump()
    }

    override fun hasApiKey(): Boolean = getApiKey().isNotBlank()

    override fun clearApiKey() {
        prefs.edit().remove(KEY_API_KEY).apply()
        Log.i(TAG, "API key cleared")
        bump()
    }

    // ── Convenience aliases (used by SettingsViewModel) ───────────────────

    fun saveApiKey(key: String) = setApiKey(key)
    fun clear() = clearApiKey()

    /** Save the active endpoint name (`"production"` / `"local_relay"` / `"dev"`). */
    fun saveEndpoint(endpoint: String) {
        prefs.edit().putString(KEY_ENDPOINT, endpoint).apply()
    }

    /** The persisted endpoint name. Defaults to `"production"`. */
    val activeEndpoint: String
        get() = prefs.getString(KEY_ENDPOINT, "production") ?: "production"

    // ── Brain server (JARVIS mode) ────────────────────────────────────────

    /** Save the JARVIS brain server base URL (e.g. `"http://10.10.0.50:8765"`). */
    fun saveBrainServerUrl(url: String) {
        prefs.edit().putString(KEY_BRAIN_URL, url.trim().trimEnd('/')).apply()
        bump()
    }

    /** The persisted brain server URL. Empty string means none configured. */
    fun getBrainServerUrl(): String = prefs.getString(KEY_BRAIN_URL, "") ?: ""

    /**
     * Optional remote TTS URL (the brain server's `POST /tts` endpoint).
     * Returns ONLY the explicit value the user typed in Settings — no
     * auto-derive, because if the brain TTS server isn't actually running
     * the request fails silently and the user gets no audio at all. Empty
     * string means "use local Android TTS".
     */
    fun getBrainTtsUrl(): String =
        prefs.getString(KEY_BRAIN_TTS_URL, "")?.trim().orEmpty()

    fun saveBrainTtsUrl(url: String) {
        prefs.edit().putString(KEY_BRAIN_TTS_URL, url.trim().trimEnd('/')).apply()
        bump()
    }

    /**
     * Edge TTS voice id — one of Microsoft's free Edge Read-Aloud voices
     * (e.g. `en-GB-RyanNeural`). Defaults to a British male voice that
     * matches the JARVIS tone if the user hasn't picked one.
     */
    fun getEdgeTtsVoice(): String =
        prefs.getString(KEY_EDGE_TTS_VOICE, DEFAULT_EDGE_VOICE)?.trim().orEmpty()
            .ifBlank { DEFAULT_EDGE_VOICE }

    fun saveEdgeTtsVoice(voice: String) {
        prefs.edit().putString(KEY_EDGE_TTS_VOICE, voice.trim()).apply()
        bump()
    }

    /**
     * Whether Edge TTS (online, Microsoft neural voices) is enabled. Off by
     * default — Microsoft's public endpoint is auth-gated and sporadically
     * returns 403 from some POPs even with the correct Sec-MS-GEC handshake,
     * so we prefer local Android TTS for reliability and let the user opt
     * into Edge when they want the nicer voice.
     */
    fun isEdgeTtsEnabled(): Boolean =
        prefs.getBoolean(KEY_EDGE_TTS_ENABLED, false)

    fun saveEdgeTtsEnabled(enabled: Boolean) {
        prefs.edit().putBoolean(KEY_EDGE_TTS_ENABLED, enabled).apply()
        bump()
    }

    // ── Groq PlayAI TTS ────────────────────────────────────────────────────
    //
    // Primary voice path. Reuses the Groq API key the user already set
    // up for chat routing — no separate key needed. Endpoint matches the
    // OpenAI /v1/audio/speech schema so the client is just an HTTPS POST.

    fun getGroqTtsVoice(): String =
        prefs.getString(KEY_GROQ_TTS_VOICE, DEFAULT_GROQ_VOICE)?.trim().orEmpty()
            .ifBlank { DEFAULT_GROQ_VOICE }

    fun saveGroqTtsVoice(voice: String) {
        prefs.edit().putString(KEY_GROQ_TTS_VOICE, voice.trim()).apply()
        bump()
    }

    /**
     * Groq TTS is enabled by default when the user has a Groq API key
     * configured (they already granted us that credential for chat). The
     * flag lets them turn it off explicitly if they'd rather use local
     * Android TTS.
     */
    fun isGroqTtsEnabled(): Boolean =
        prefs.getBoolean(KEY_GROQ_TTS_ENABLED, true)

    fun saveGroqTtsEnabled(enabled: Boolean) {
        prefs.edit().putBoolean(KEY_GROQ_TTS_ENABLED, enabled).apply()
        bump()
    }

    // ── Per-model inference config ────────────────────────────────────────
    //
    // Accelerator / sampler / context tuning the user sets from the
    // ModelConfigDialog. JSON-serialised under `model_config:<id>` so each
    // model remembers its own knobs across launches.

    fun getModelConfig(modelId: String): com.jarvis.android.domain.model.ModelConfig {
        val json = prefs.getString("$KEY_MODEL_CONFIG_PREFIX$modelId", null)
            ?: return com.jarvis.android.domain.model.ModelConfig()
        return runCatching {
            kotlinx.serialization.json.Json.decodeFromString<com.jarvis.android.domain.model.ModelConfig>(json)
        }.getOrElse {
            Log.w(TAG, "Corrupt model config for $modelId — using defaults")
            com.jarvis.android.domain.model.ModelConfig()
        }
    }

    fun saveModelConfig(modelId: String, config: com.jarvis.android.domain.model.ModelConfig) {
        val json = kotlinx.serialization.json.Json.encodeToString(
            com.jarvis.android.domain.model.ModelConfig.serializer(),
            config,
        )
        prefs.edit().putString("$KEY_MODEL_CONFIG_PREFIX$modelId", json).apply()
        bump()
    }

    /**
     * Active connection mode.
     * - `"anthropic"` — direct Anthropic API (default)
     * - `"brain"`     — route through JARVIS brain server
     */
    fun saveConnectionMode(mode: String) {
        prefs.edit().putString(KEY_CONN_MODE, mode).apply()
    }

    val connectionMode: String
        get() = prefs.getString(KEY_CONN_MODE, "anthropic") ?: "anthropic"

    // ── HuggingFace token (for gated model downloads) ─────────────────────
    //
    // Gated HF repos (Gemma, Llama, some Mistral variants) require an HF
    // access token even for public download URLs. The token is attached as
    // `Authorization: Bearer <token>` on requests whose host is huggingface.co.

    fun getHfToken(): String {
        val saved = prefs.getString(KEY_HF_TOKEN, "") ?: ""
        if (saved.isNotBlank()) return saved
        // Personal-use fallback: token baked into the APK at build time via
        // local.properties → BuildConfig.DEFAULT_HF_TOKEN. Survives uninstall,
        // never touches git. Blank string means no fallback configured.
        return BuildConfig.DEFAULT_HF_TOKEN
    }

    fun saveHfToken(token: String) {
        prefs.edit().putString(KEY_HF_TOKEN, token.trim()).apply()
        Log.i(TAG, "HF token saved (${token.take(6)}…)")
    }

    fun clearHfToken() {
        prefs.edit().remove(KEY_HF_TOKEN).apply()
        Log.i(TAG, "HF token cleared")
    }

    // ── Direct multi-provider (OpenAI-compatible) ─────────────────────────
    //
    // In `connectionMode = "anthropic"` (which the UI now labels "Direct Cloud")
    // the user picks an upstream provider. ANTHROPIC uses the existing native
    // Claude path. Every other provider speaks OpenAI-compatible HTTP via
    // OpenAiCompatApiService, keyed by [directProvider] + [directModel].

    private fun keyPrefName(provider: CloudProvider) = "provider_key_${provider.name.lowercase()}"

    /** Fetch the stored API key for [provider]. Empty string if none. */
    fun getProviderKey(provider: CloudProvider): String {
        // Anthropic keeps its legacy pref name so existing saves survive.
        if (provider == CloudProvider.ANTHROPIC) return getApiKey()
        return prefs.getString(keyPrefName(provider), "") ?: ""
    }

    fun saveProviderKey(provider: CloudProvider, key: String) {
        if (provider == CloudProvider.ANTHROPIC) { saveApiKey(key); return }
        prefs.edit().putString(keyPrefName(provider), key.trim()).apply()
        Log.i(TAG, "${provider.displayName} key saved (${key.take(6)}…)")
        bump()
    }

    fun clearProviderKey(provider: CloudProvider) {
        if (provider == CloudProvider.ANTHROPIC) { clearApiKey(); return }
        prefs.edit().remove(keyPrefName(provider)).apply()
        Log.i(TAG, "${provider.displayName} key cleared")
        bump()
    }

    // Override the ApiKeyProvider interface defaults so the chat top-bar picker
    // (which calls hasApiKey(provider) through the interface) actually sees the
    // per-provider keys saved via saveProviderKey. Without these overrides the
    // interface default returns empty for every non-Anthropic provider, so the
    // home-bar dropdown silently hides Groq/OpenAI/DeepSeek/xAI/OpenRouter/Mistral.
    //
    // For JARVIS_BRAIN the "key" is the brain server URL — it doesn't share a
    // pref slot with the cloud-provider keys.
    override fun getApiKey(provider: CloudProvider): String =
        if (provider == CloudProvider.JARVIS_BRAIN) getBrainServerUrl() else getProviderKey(provider)

    override fun hasApiKey(provider: CloudProvider): Boolean =
        if (provider == CloudProvider.JARVIS_BRAIN) getBrainServerUrl().isNotBlank()
        else getProviderKey(provider).isNotBlank()

    /**
     * Active direct-cloud provider. Defaults to GROQ on fresh installs because
     * the user's primary daily-driver is Groq's GPT-OSS 120B (see project
     * memory: "Groq is primary, DeepSeek is secondary"). Existing installs
     * keep whatever was previously persisted.
     */
    var directProvider: CloudProvider
        get() = runCatching {
            CloudProvider.valueOf(prefs.getString(KEY_DIRECT_PROVIDER, null) ?: CloudProvider.GROQ.name)
        }.getOrDefault(CloudProvider.GROQ)
        set(value) { prefs.edit().putString(KEY_DIRECT_PROVIDER, value.name).apply() }

    /**
     * Active model ID for the current [directProvider]. Empty → use the
     * provider's first catalog entry (see CloudModel.CATALOG). For GROQ the
     * default is `openai/gpt-oss-120b` (the user's preferred daily driver).
     */
    fun getDirectModel(provider: CloudProvider): String {
        val saved = prefs.getString("${KEY_DIRECT_MODEL}_${provider.name.lowercase()}", "") ?: ""
        if (saved.isNotBlank()) return saved
        if (provider == CloudProvider.GROQ) return "openai/gpt-oss-120b"
        return ""
    }

    fun saveDirectModel(provider: CloudProvider, modelId: String) {
        prefs.edit().putString("${KEY_DIRECT_MODEL}_${provider.name.lowercase()}", modelId).apply()
    }

    companion object {
        private const val TAG              = "ApiKeyProviderImpl"
        private const val PREFS_FILE       = "jarvis_secure_prefs"
        private const val KEY_API_KEY      = "anthropic_api_key"
        private const val KEY_ENDPOINT     = "active_endpoint"
        private const val KEY_BRAIN_URL    = "brain_server_url"
        private const val KEY_CONN_MODE    = "connection_mode"
        private const val KEY_HF_TOKEN     = "hf_token"
        private const val KEY_DIRECT_PROVIDER = "direct_provider"
        private const val KEY_DIRECT_MODEL    = "direct_model"
        private const val KEY_BRAIN_TTS_URL   = "brain_tts_url"
        private const val KEY_EDGE_TTS_VOICE   = "edge_tts_voice"
        private const val KEY_EDGE_TTS_ENABLED = "edge_tts_enabled"
        private const val KEY_GROQ_TTS_VOICE   = "groq_tts_voice"
        private const val KEY_GROQ_TTS_ENABLED = "groq_tts_enabled"
        /**
         * Warm male Orpheus v1 voice — Groq's successor to the
         * decommissioned PlayAI voice catalog. Closest to the old
         * "Fritz-PlayAI" JARVIS default. Legacy prefs keyed under
         * `Fritz-PlayAI` etc are migrated at read time in
         * [com.jarvis.android.presentation.settings.GroqTtsVoice.migrateLegacyVoiceId].
         */
        const val DEFAULT_GROQ_VOICE           = "troy"
        /** Prefix for per-model inference config entries: `model_config:<modelId>`. */
        private const val KEY_MODEL_CONFIG_PREFIX = "model_config:"
        /** British male, closest to the Iron Man JARVIS tone. */
        const val DEFAULT_EDGE_VOICE          = "en-GB-RyanNeural"
        private const val KEY_DEFAULT_MIGRATION = "migrated_default_to_groq_v1"
    }
}
