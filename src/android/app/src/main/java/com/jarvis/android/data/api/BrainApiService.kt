package com.jarvis.android.data.api

import android.util.Log
import com.jarvis.android.core.network.RawSseEvent
import com.jarvis.android.core.network.SseClient
import com.jarvis.android.data.repository.ApiKeyProviderImpl
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.transform
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Streams a chat message to the JARVIS bridge at `/api/page-query`.
 *
 * The bridge runs the LLM call server-side against whichever model is active
 * (chosen via `/api/model` — see [pinProvider]). The Android client receives
 * streamed text chunks only.
 *
 * SSE format emitted by the bridge:
 *   data: {"type":"text","content":"chunk..."}
 *   data: {"type":"done"}
 *   data: {"type":"error","content":"message"}   (on failure)
 */
@Singleton
class BrainApiService @Inject constructor(
    private val sseClient:          SseClient,
    private val apiKeyProviderImpl: ApiKeyProviderImpl,
    private val okHttpClient:       OkHttpClient,
) {

    private val json = Json { ignoreUnknownKeys = true }

    /**
     * Send [query] to the bridge and stream back text chunks as a [Flow].
     * The flow completes when the server emits `{"type":"done"}`.
     */
    fun streamMessage(query: String): Flow<String> {
        val baseUrl = apiKeyProviderImpl.getBrainServerUrl().trimEnd('/')
        val url     = "$baseUrl/api/page-query"

        Log.d(TAG, "streamMessage → $url (${query.length}ch)")

        // pageContent / mentionedTabs are empty on mobile — no DOM to attach.
        val body = json.encodeToString(PageQueryRequest(query = query))
        val request = Request.Builder()
            .url(url)
            .post(body.toRequestBody(JSON_MEDIA_TYPE))
            .build()

        return sseClient.stream(request)
            .transform { raw ->
                when (raw) {
                    is RawSseEvent.Message -> {
                        val data = raw.data.trim()
                        if (data.isEmpty()) return@transform
                        try {
                            val chunk = json.decodeFromString<BridgeChunk>(data)
                            when (chunk.type) {
                                "text"  -> if (chunk.content.isNotEmpty()) emit(chunk.content)
                                "error" -> throw IOException("Bridge error: ${chunk.content}")
                                "done"  -> return@transform
                                else    -> { /* ignore unknown event types */ }
                            }
                        } catch (e: IOException) {
                            throw e
                        } catch (_: Exception) {
                            // Ignore malformed lines
                        }
                    }
                    is RawSseEvent.Complete -> Unit
                    is RawSseEvent.Failure  -> {
                        val msg = raw.message ?: raw.t?.message ?: "Bridge connection failed"
                        throw IOException(msg)
                    }
                }
            }
    }

    // ── Model management ──────────────────────────────────────────────────────
    // Preserves the external "provider" terminology so callers (SettingsViewModel,
    // SettingsScreen) don't change. Internally we map to the bridge's model list.

    /**
     * Fetch every model the bridge exposes plus the currently active one.
     * Returns null if the server is unreachable.
     */
    suspend fun listProviders(): BrainProvidersResponse? = withContext(Dispatchers.IO) {
        val baseUrl = apiKeyProviderImpl.getBrainServerUrl().trimEnd('/')
        if (baseUrl.isBlank()) return@withContext null
        try {
            val request = Request.Builder().url("$baseUrl/api/models").get().build()
            okHttpClient.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return@withContext null
                val body = response.body?.string() ?: return@withContext null
                val parsed = json.decodeFromString<BridgeModelsResponse>(body)
                BrainProvidersResponse(
                    providers = parsed.models.map {
                        BrainProvider(name = it.name, model = it.name, enabled = true, priority = 0)
                    },
                    pinned    = parsed.active,
                )
            }
        } catch (e: Exception) {
            Log.w(TAG, "listProviders failed: ${e.message}")
            null
        }
    }

    /**
     * Set [providerName] as the active model on the bridge. Empty string is a
     * no-op (bridge doesn't support "clear pin" — the registry default stands).
     * Returns true on success.
     */
    suspend fun pinProvider(providerName: String): Boolean = withContext(Dispatchers.IO) {
        val baseUrl = apiKeyProviderImpl.getBrainServerUrl().trimEnd('/')
        if (baseUrl.isBlank() || providerName.isBlank()) return@withContext false
        try {
            val body = json.encodeToString(ModelPinRequest(model = providerName))
            val request = Request.Builder()
                .url("$baseUrl/api/model")
                .post(body.toRequestBody(JSON_MEDIA_TYPE))
                .build()
            okHttpClient.newCall(request).execute().use { it.isSuccessful }
        } catch (e: Exception) {
            Log.w(TAG, "pinProvider failed: ${e.message}")
            false
        }
    }

    companion object {
        private const val TAG = "BrainApiService"
        private val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()
    }
}

@Serializable
private data class PageQueryRequest(
    val query:       String,
    val pageContent: String? = null,
)

@Serializable
private data class BridgeChunk(
    val type:    String = "",
    val content: String = "",
)

@Serializable
private data class BridgeModelsResponse(
    val models: List<BridgeModel> = emptyList(),
    val active: String            = "",
)

@Serializable
private data class BridgeModel(val name: String = "")

@Serializable
private data class ModelPinRequest(val model: String)

@Serializable
data class BrainProvidersResponse(
    val providers: List<BrainProvider> = emptyList(),
    val pinned:    String              = "",
)

@Serializable
data class BrainProvider(
    val name:     String  = "",
    val model:    String  = "",
    val enabled:  Boolean = true,
    val priority: Int     = 0,
)
