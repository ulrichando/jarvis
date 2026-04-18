package com.jarvis.android.di

import android.content.Context
import android.util.Log
import com.jarvis.android.core.network.ApiKeyInterceptor
import com.jarvis.android.core.network.ApiKeyProvider
import com.jarvis.android.core.network.SseClient
import com.jarvis.android.data.api.ClaudeApiService
import com.jarvis.android.data.api.SseParser
import com.jarvis.android.data.repository.ApiKeyProviderImpl
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import okhttp3.OkHttpClient
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object NetworkModule {

    /**
     * OkHttp client shared by [SseClient] and any future Retrofit services.
     *
     * Timeouts:
     *   - connect: 30 s
     *   - read/write: 0 (infinite) — streaming SSE responses have no time bound
     *
     * [ApiKeyInterceptor] injects `x-api-key`, `anthropic-version`, and
     * `anthropic-beta` headers into every outgoing request.
     */
    @Provides
    @Singleton
    fun provideOkHttpClient(apiKeyProvider: ApiKeyProvider): OkHttpClient =
        OkHttpClient.Builder()
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.SECONDS)       // streaming — no read timeout
            .writeTimeout(30, TimeUnit.SECONDS)
            .addInterceptor(ApiKeyInterceptor(apiKeyProvider))
            .build()

    @Provides
    @Singleton
    fun provideSseClient(okHttpClient: OkHttpClient): SseClient =
        SseClient(okHttpClient)

    /**
     * Reads [endpoint] configuration from `assets/endpoints.json` and returns
     * the messages URL for the active endpoint (stored via [ApiKeyProviderImpl]).
     *
     * Falls back to the production URL if the asset cannot be parsed.
     */
    @Provides
    @Singleton
    fun provideClaudeApiService(
        @ApplicationContext context: Context,
        okHttpClient: OkHttpClient,
        sseClient: SseClient,
        sseParser: SseParser,
        apiKeyProvider: ApiKeyProvider,
        apiKeyProviderImpl: ApiKeyProviderImpl,
    ): ClaudeApiService {
        val service = ClaudeApiService(
            context          = context,
            okHttpClient     = okHttpClient,
            sseClient        = sseClient,
            sseParser        = sseParser,
            apiKeyProvider   = apiKeyProvider,
        )
        service.messagesUrl = resolveMessagesUrl(context, apiKeyProviderImpl.activeEndpoint)
        return service
    }

    // ── Endpoint URL resolution ───────────────────────────────────────────

    /**
     * Parse `assets/endpoints.json` and return the `url` field for [endpointName].
     *
     * Expected asset format:
     * ```json
     * {
     *   "production":  { "url": "https://api.anthropic.com/v1/messages", ... },
     *   "local_relay": { "url": "http://10.10.0.50:8765/api/llm",        ... },
     *   "dev":         { "url": "http://10.0.2.2:8765/api/llm",          ... }
     * }
     * ```
     */
    private fun resolveMessagesUrl(context: Context, endpointName: String): String {
        return try {
            val json = context.assets.open("endpoints.json")
                .bufferedReader()
                .readText()
            JSONObject(json)
                .getJSONObject(endpointName)
                .getString("url")
        } catch (e: Exception) {
            Log.w("NetworkModule", "endpoints.json parse failed, using production: ${e.message}")
            "https://api.anthropic.com/v1/messages"
        }
    }
}
