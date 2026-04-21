package com.jarvis.android.core.network

import okhttp3.Interceptor
import okhttp3.Response
import javax.inject.Inject
import javax.inject.Singleton

/**
 * OkHttp interceptor that attaches required Anthropic API headers to every request.
 *
 * Headers added:
 *   x-api-key         : user's API key (from [ApiKeyProvider])
 *   anthropic-version : "2023-06-01" (locked; bump when we adopt new API features)
 *   content-type      : "application/json" (belt-and-suspenders; Retrofit sets this too)
 *
 * Security invariants:
 *   - The API key is NEVER logged (not here, not via OkHttp's logging interceptor).
 *   - The [ApiKeyProvider] abstraction keeps the raw key out of this class; this
 *     interceptor never holds the key itself.
 *   - OkHttp's HttpLoggingInterceptor is added at BASIC level in debug only, and
 *     added AFTER this interceptor in the chain so headers are already attached
 *     (but the logging interceptor sees them — which is why we use BASIC, not HEADERS).
 */
@Singleton
class ApiKeyInterceptor @Inject constructor(
    private val apiKeyProvider: ApiKeyProvider,
) : Interceptor {

    override fun intercept(chain: Interceptor.Chain): Response {
        val key = apiKeyProvider.getApiKey()

        val request = chain.request().newBuilder()
            .header("x-api-key",         key)
            .header("anthropic-version",  ANTHROPIC_VERSION)
            .header("content-type",       "application/json")
            // Beta header — enables extended thinking, tool use improvements, etc.
            // Remove if not using any beta features.
            .header("anthropic-beta",     "interleaved-thinking-2025-05-14")
            .build()

        return chain.proceed(request)
    }

    private companion object {
        const val ANTHROPIC_VERSION = "2023-06-01"
    }
}

/**
 * Abstraction over API key storage.
 * Implemented by [com.jarvis.android.data.repository.ApiKeyProviderImpl] which
 * reads from [EncryptedSharedPreferences].
 *
 * The interface exists so unit tests can inject a fake without touching crypto.
 */
interface ApiKeyProvider {
    /** Returns the stored API key, or empty string if not yet set. */
    fun getApiKey(): String

    /** Persists the API key. Throws if storage is unavailable. */
    fun setApiKey(key: String)

    /** True if an API key has been stored. */
    fun hasApiKey(): Boolean

    /** Wipes the stored API key. */
    fun clearApiKey()

    /**
     * Per-provider key accessors — lets the home-bar picker filter its cloud
     * catalog to "providers the user has actually enabled".
     *
     * For now only Anthropic has a plumbed storage slot (the existing
     * [getApiKey]/[setApiKey] pair), so these forward to that pair when
     * [provider] is Anthropic and return empty/false otherwise. As we add
     * Settings rows for DeepSeek / Groq / OpenAI / etc. we wire each to its
     * own EncryptedSharedPreferences slot here.
     */
    fun getApiKey(provider: com.jarvis.android.domain.model.CloudProvider): String =
        if (provider == com.jarvis.android.domain.model.CloudProvider.ANTHROPIC) getApiKey() else ""

    fun hasApiKey(provider: com.jarvis.android.domain.model.CloudProvider): Boolean =
        getApiKey(provider).isNotBlank()
}
