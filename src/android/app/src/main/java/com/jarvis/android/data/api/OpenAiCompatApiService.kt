package com.jarvis.android.data.api

import android.util.Log
import com.jarvis.android.core.network.RawSseEvent
import com.jarvis.android.core.network.SseClient
import com.jarvis.android.data.api.dto.ContentBlockDto
import com.jarvis.android.data.api.dto.MessageDto
import com.jarvis.android.data.api.dto.ToolDefinitionDto
import com.jarvis.android.domain.model.CloudProvider
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.transform
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Streaming chat client for OpenAI-compatible providers — now with full
 * function/tool calling. Every provider we support (OpenAI, Groq, DeepSeek,
 * xAI, OpenRouter, Mistral) speaks the same `/v1/chat/completions` dialect,
 * including the `tools` parameter and the streamed `tool_calls` deltas, so
 * the same code path handles all of them.
 *
 * Output is a [Flow] of [OpenAiStreamEvent]. The caller runs the agent loop
 * (execute tools → append results → re-call streamChat) the same way
 * ChatRepositoryImpl does for Anthropic.
 */
@Singleton
class OpenAiCompatApiService @Inject constructor(
    private val sseClient: SseClient,
) {

    private val json = Json {
        ignoreUnknownKeys = true
        encodeDefaults    = false
        explicitNulls     = false
    }

    // ── Public API ──────────────────────────────────────────────────────

    /**
     * Stream a chat completion. [tools] is the Anthropic-shaped tool list
     * (same one ClaudeApiService uses) — we translate to the OpenAI
     * `function` schema on the way out, and back from OpenAI `tool_calls`
     * on the way in.
     */
    fun streamChat(
        provider: CloudProvider,
        apiKey:   String,
        model:    String,
        system:   String,
        messages: List<MessageDto>,
        tools:    List<ToolDefinitionDto> = emptyList(),
    ): Flow<OpenAiStreamEvent> = flow {
        if (apiKey.isBlank()) {
            throw IllegalStateException("${provider.displayName}: no API key configured. Set it in Settings.")
        }
        val baseUrl = baseUrlFor(provider)
        val bodyJson = buildRequestJson(model, system, messages, tools)
        Log.d(TAG, "streamChat ${provider.name} model=$model msgs=${messages.size} tools=${tools.size} body=${bodyJson.length}b")

        val httpRequest = Request.Builder()
            .url("$baseUrl/chat/completions")
            .post(bodyJson.toRequestBody(JSON_MEDIA_TYPE))
            .header("Authorization", "Bearer $apiKey")
            .header("Content-Type",  "application/json")
            .apply {
                if (provider == CloudProvider.OPENROUTER) {
                    header("HTTP-Referer", "https://jarvis.local")
                    header("X-Title",      "JARVIS Android")
                }
            }
            .build()

        sseClient.stream(httpRequest).transform<RawSseEvent, OpenAiStreamEvent> { raw ->
            when (raw) {
                is RawSseEvent.Message -> {
                    val events = parseFrame(raw.data)
                    events.forEach { emit(it) }
                }
                is RawSseEvent.Complete -> Unit
                is RawSseEvent.Failure  -> emit(
                    OpenAiStreamEvent.Error(
                        "${provider.displayName} stream error: ${raw.message ?: raw.t?.message ?: "network_error"}",
                    ),
                )
            }
        }.collect { emit(it) }
    }

    // ── Wire-format translation ─────────────────────────────────────────

    /** OpenAI-compatible `/v1` base URL per provider. */
    private fun baseUrlFor(provider: CloudProvider): String = when (provider) {
        CloudProvider.OPENAI     -> "https://api.openai.com/v1"
        CloudProvider.GROQ       -> "https://api.groq.com/openai/v1"
        CloudProvider.DEEPSEEK   -> "https://api.deepseek.com/v1"
        CloudProvider.XAI        -> "https://api.x.ai/v1"
        CloudProvider.OPENROUTER -> "https://openrouter.ai/api/v1"
        CloudProvider.MISTRAL    -> "https://api.mistral.ai/v1"
        CloudProvider.ANTHROPIC,
        CloudProvider.GOOGLE,
        CloudProvider.JARVIS_BRAIN -> error("${provider.name} is not served by this client")
    }

    /** Serialize the whole request body: model + messages + tools + stream flag. */
    private fun buildRequestJson(
        model:    String,
        system:   String,
        messages: List<MessageDto>,
        tools:    List<ToolDefinitionDto>,
    ): String {
        val obj = buildJsonObject {
            put("model", model)
            put("stream", true)
            put("messages", buildJsonArray {
                if (system.isNotBlank()) add(buildJsonObject {
                    put("role", "system")
                    put("content", system)
                })
                messagesToJson(messages).forEach { add(it) }
            })
            if (tools.isNotEmpty()) {
                put("tools", buildJsonArray {
                    tools.forEach { add(toolToJson(it)) }
                })
                put("tool_choice", "auto")
            }
        }
        return json.encodeToString(JsonObject.serializer(), obj)
    }

    /** Translate JARVIS [MessageDto] → OpenAI chat messages, preserving
     *  tool_calls on assistant turns, tool results as role:"tool" rows, and
     *  base64 images as multi-part content (text + image_url) so vision-
     *  capable models (GPT-4/5, Gemini-via-OpenRouter, Claude-via-OpenRouter,
     *  Pixtral on Mistral) actually see the attached picture.
     */
    private fun messagesToJson(messages: List<MessageDto>): List<JsonObject> {
        val out = mutableListOf<JsonObject>()
        messages.forEach { m ->
            val toolUses    = m.content.filterIsInstance<ContentBlockDto.ToolUse>()
            val toolResults = m.content.filterIsInstance<ContentBlockDto.ToolResult>()
            val text        = m.content.filterIsInstance<ContentBlockDto.Text>()
                .joinToString("") { it.text }
            val images      = m.content.filterIsInstance<ContentBlockDto.Image>()

            when {
                m.role == "assistant" && toolUses.isNotEmpty() -> {
                    out += buildJsonObject {
                        put("role", "assistant")
                        if (text.isNotBlank()) put("content", text) else put("content", JsonNull)
                        put("tool_calls", buildJsonArray {
                            toolUses.forEach { tu ->
                                add(buildJsonObject {
                                    put("id", tu.id)
                                    put("type", "function")
                                    put("function", buildJsonObject {
                                        put("name", tu.name)
                                        put(
                                            "arguments",
                                            json.encodeToString(JsonObject.serializer(), tu.input),
                                        )
                                    })
                                })
                            }
                        })
                    }
                }
                m.role == "user" && toolResults.isNotEmpty() -> {
                    toolResults.forEach { tr ->
                        out += buildJsonObject {
                            put("role", "tool")
                            put("tool_call_id", tr.toolUseId)
                            put("content", tr.content)
                        }
                    }
                    if (text.isNotBlank()) {
                        out += buildJsonObject {
                            put("role", "user")
                            put("content", text)
                        }
                    }
                }
                images.isNotEmpty() -> {
                    // Multi-part content when images are present — OpenAI /
                    // Groq / OpenRouter / Mistral all accept the same shape:
                    //   content: [{type:"text",...},{type:"image_url",...}]
                    out += buildJsonObject {
                        put("role", m.role)
                        put("content", buildJsonArray {
                            if (text.isNotBlank()) add(buildJsonObject {
                                put("type", "text")
                                put("text", text)
                            })
                            images.forEach { img ->
                                val mime = img.source.mediaType.ifBlank { "image/jpeg" }
                                val b64  = img.source.data
                                add(buildJsonObject {
                                    put("type", "image_url")
                                    put("image_url", buildJsonObject {
                                        put("url", "data:$mime;base64,$b64")
                                    })
                                })
                            }
                        })
                    }
                }
                text.isNotBlank() -> {
                    out += buildJsonObject {
                        put("role", m.role)
                        put("content", text)
                    }
                }
            }
        }
        return out
    }

    /** Anthropic tool def → OpenAI function schema. */
    private fun toolToJson(def: ToolDefinitionDto): JsonObject = buildJsonObject {
        put("type", "function")
        put("function", buildJsonObject {
            put("name", def.name)
            put("description", def.description)
            put("parameters", def.inputSchema)
        })
    }

    // ── Streaming parser ────────────────────────────────────────────────

    /** Decode a single SSE `data:` frame into zero or more stream events. */
    private fun parseFrame(dataLine: String): List<OpenAiStreamEvent> {
        val trimmed = dataLine.trim()
        if (trimmed.isEmpty() || trimmed == "[DONE]") return emptyList()
        return try {
            val root    = json.parseToJsonElement(trimmed).jsonObject
            val choices = root["choices"]?.jsonArray ?: return emptyList()
            val choice  = choices.firstOrNull()?.jsonObject ?: return emptyList()
            val out     = mutableListOf<OpenAiStreamEvent>()

            val delta = choice["delta"]?.jsonObject ?: choice["message"]?.jsonObject
            if (delta != null) {
                // Text delta
                val content = delta["content"]?.takeIf { it !is JsonNull }
                    ?.jsonPrimitive?.contentOrNull()
                if (!content.isNullOrEmpty()) out += OpenAiStreamEvent.Text(content)

                // Tool-call deltas — array; each entry has an `index` to merge
                // against earlier partial chunks.
                val toolCalls = delta["tool_calls"]?.jsonArray
                if (toolCalls != null) {
                    toolCalls.forEach { el ->
                        val o       = el.jsonObject
                        val index   = o["index"]?.jsonPrimitive?.contentOrNull()?.toIntOrNull() ?: 0
                        val id      = o["id"]?.jsonPrimitive?.contentOrNull()
                        val fn      = o["function"]?.jsonObject
                        val name    = fn?.get("name")?.jsonPrimitive?.contentOrNull()
                        val argsDel = fn?.get("arguments")?.jsonPrimitive?.contentOrNull()
                        out += OpenAiStreamEvent.ToolCallDelta(index, id, name, argsDel)
                    }
                }
            }

            choice["finish_reason"]?.jsonPrimitive?.contentOrNull()?.let {
                out += OpenAiStreamEvent.Done(it)
            }
            out
        } catch (e: Exception) {
            Log.w(TAG, "SSE frame parse failed: ${e.message} · body=${trimmed.take(160)}")
            emptyList()
        }
    }

    companion object {
        private const val TAG       = "OpenAiCompatApi"
        private val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()
    }
}

// ── Stream event types ────────────────────────────────────────────────────────

/**
 * A single event on the OpenAI-compatible streaming channel. Text deltas
 * arrive as [Text]; tool calls are streamed piece-by-piece via [ToolCallDelta]
 * (merged by `index` on the caller side); [Done] carries the finish reason
 * (`stop`, `tool_calls`, `length`, …) so the caller knows whether to run the
 * agent loop or terminate.
 */
sealed interface OpenAiStreamEvent {
    data class Text(val delta: String) : OpenAiStreamEvent
    data class ToolCallDelta(
        val index:     Int,
        val id:        String?,
        val name:      String?,
        val argsDelta: String?,
    ) : OpenAiStreamEvent
    data class Done(val finishReason: String?) : OpenAiStreamEvent
    data class Error(val message: String) : OpenAiStreamEvent
}

// kotlinx.serialization's JsonPrimitive.content throws on JsonNull; this
// variant returns null instead, which the parsers above all expect.
private fun kotlinx.serialization.json.JsonPrimitive.contentOrNull(): String? =
    if (this is JsonNull) null else content
