package com.jarvis.android.data.api

import android.content.Context
import android.util.Log
import com.jarvis.android.core.network.ApiKeyProvider
import com.jarvis.android.core.network.RawSseEvent
import com.jarvis.android.core.network.SseClient
import com.jarvis.android.data.api.dto.ContentBlockDto
import com.jarvis.android.data.api.dto.ContentBlockStartData
import com.jarvis.android.data.api.dto.ContentDelta
import com.jarvis.android.data.api.dto.MessageDto
import com.jarvis.android.data.api.dto.MessageRequestDto
import com.jarvis.android.data.api.dto.SseStreamEvent
import com.jarvis.android.data.api.dto.ToolDefinitionDto
import com.jarvis.android.system.tools.ToolResultBlock
import com.jarvis.android.system.tools.ToolUseBlock
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.filterNotNull
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.transform
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Thin wrapper over [SseClient] that translates between the JARVIS domain types
 * and the Claude Messages API wire format.
 *
 * ## Streaming workflow
 * ```
 * ClaudeApiService.streamMessage(...)
 *   → Flow<SseStreamEvent>
 *     collected by ChatRepository
 *       → accumulates AssistantTurn (text + tool_use blocks)
 *         → JarvisToolDispatcher.dispatch() per tool_use
 *           → tool_results fed back as next user message
 *             → repeat until stop_reason == "end_turn"
 * ```
 *
 * ## Endpoint selection
 * Reads `endpoints.json` from assets. The active endpoint is controlled by
 * [EndpointConfig] which is set in Settings and persisted via DataStore.
 * Defaults to `production` (api.anthropic.com).
 *
 * ## API key
 * Injected via [ApiKeyProvider] which reads from [EncryptedSharedPreferences].
 * The key is added to every request by [ApiKeyInterceptor] in the OkHttp chain.
 */
@Singleton
class ClaudeApiService @Inject constructor(
    @ApplicationContext private val context: Context,
    private val okHttpClient: OkHttpClient,
    private val sseClient: SseClient,
    private val sseParser: SseParser,
    private val apiKeyProvider: ApiKeyProvider,
) {

    private val json = Json {
        ignoreUnknownKeys  = true
        encodeDefaults     = false
        explicitNulls      = false
    }

    // ── Configuration (injected by DI in D19) ─────────────────────────────

    /**
     * Base URL for the messages endpoint.
     * Swapped at runtime by the Settings screen (production / relay / dev).
     */
    var messagesUrl: String = "https://api.anthropic.com/v1/messages"

    // ── Streaming messages ────────────────────────────────────────────────

    /**
     * Send a conversation turn and stream the assistant response.
     *
     * @param messages  Full conversation history including the latest user message.
     * @param system    System prompt (loaded from `jarvis_persona.txt`).
     * @param tools     Tool definitions to expose. Defaults to all 16 JARVIS tools.
     * @param model     Model ID. Defaults to `claude-sonnet-4-6`.
     * @param maxTokens Hard token budget.
     *
     * @return [Flow] of [SseStreamEvent]s. Collect until [SseStreamEvent.MessageStop].
     *         Cancelling the flow sends a `cancel()` to the underlying SSE connection.
     */
    fun streamMessage(
        messages:  List<MessageDto>,
        system:    String,
        tools:     List<ToolDefinitionDto> = ToolDefinitions.ALL,
        model:     String = DEFAULT_MODEL,
        maxTokens: Int    = DEFAULT_MAX_TOKENS,
    ): Flow<SseStreamEvent> {
        val requestBody = MessageRequestDto(
            model     = model,
            maxTokens = maxTokens,
            system    = system,
            messages  = messages,
            tools     = tools,
            stream    = true,
        )

        val bodyJson = json.encodeToString(requestBody)
        Log.d(TAG, "streamMessage: model=$model msgs=${messages.size} body=${bodyJson.length}b")

        val httpRequest = Request.Builder()
            .url(messagesUrl)
            .post(bodyJson.toRequestBody(JSON_MEDIA_TYPE))
            .header("anthropic-beta", "interleaved-thinking-2025-05-14")
            .build()

        return sseClient.stream(httpRequest)
            .transform { raw ->
                when (raw) {
                    is RawSseEvent.Message  -> {
                        val event = sseParser.parse(raw)
                        if (event != null) emit(event)
                    }
                    is RawSseEvent.Complete -> emit(SseStreamEvent.MessageStop)
                    is RawSseEvent.Failure  -> {
                        val msg = raw.message ?: raw.t?.message ?: "SSE failure"
                        emit(SseStreamEvent.StreamError(
                            com.jarvis.android.data.api.dto.ApiError("network_error", msg)
                        ))
                    }
                }
            }
    }

    // ── Message builders ──────────────────────────────────────────────────

    /** Build a plain user text message. */
    fun userMessage(text: String): MessageDto = MessageDto(
        role    = "user",
        content = listOf(ContentBlockDto.Text(text)),
    )

    /** Build a user message that carries tool results back to the model. */
    fun toolResultMessage(results: List<ToolResultBlock>): MessageDto = MessageDto(
        role = "user",
        content = results.map { r ->
            ContentBlockDto.ToolResult(
                toolUseId = r.toolUseId,
                content   = r.content,
                isError   = r.isError,
            )
        },
    )

    /**
     * Build an assistant message from an accumulated streaming turn.
     * This is appended to history before tool results so the model can
     * see its own tool_use decisions.
     */
    fun assistantMessage(
        textBlocks:   List<String>,
        toolUseBlocks: List<ToolUseBlock>,
    ): MessageDto {
        val content = mutableListOf<ContentBlockDto>()
        textBlocks.forEach    { content.add(ContentBlockDto.Text(it)) }
        toolUseBlocks.forEach { content.add(ContentBlockDto.ToolUse(it.id, it.name, it.input)) }
        return MessageDto(role = "assistant", content = content)
    }

    // ── Stream accumulator ────────────────────────────────────────────────

    /**
     * Accumulates streaming [SseStreamEvent]s into a complete [AssistantTurn].
     *
     * Usage — collect from [streamMessage] and feed each event here:
     * ```kotlin
     * val acc = StreamAccumulator()
     * streamMessage(...).collect { event ->
     *     acc.feed(event)
     *     if (event is SseStreamEvent.MessageStop) {
     *         processCompleteTurn(acc.build())
     *     }
     * }
     * ```
     */
    inner class StreamAccumulator {
        private val textBuilders = mutableMapOf<Int, StringBuilder>()
        private val toolBuilders = mutableMapOf<Int, ToolBlockBuilder>()
        private val blockMeta    = mutableMapOf<Int, ContentBlockStartData>()

        var stopReason: String? = null
        var inputTokens:  Int  = 0
        var outputTokens: Int  = 0

        fun feed(event: SseStreamEvent) {
            when (event) {
                is SseStreamEvent.MessageStart -> {
                    inputTokens  = event.message.usage?.inputTokens  ?: 0
                    outputTokens = event.message.usage?.outputTokens ?: 0
                }
                is SseStreamEvent.ContentBlockStart -> {
                    blockMeta[event.index] = event.contentBlock
                    when (event.contentBlock.type) {
                        "text"     -> textBuilders[event.index] = StringBuilder()
                        "tool_use" -> toolBuilders[event.index] = ToolBlockBuilder(
                            id   = event.contentBlock.id   ?: "",
                            name = event.contentBlock.name ?: "",
                        )
                    }
                }
                is SseStreamEvent.ContentBlockDelta -> {
                    when (val d = event.delta) {
                        is ContentDelta.TextDelta       -> textBuilders[event.index]?.append(d.text)
                        is ContentDelta.InputJsonDelta  -> toolBuilders[event.index]?.append(d.partialJson)
                    }
                }
                is SseStreamEvent.MessageDelta -> {
                    stopReason    = event.delta.stopReason
                    outputTokens += event.usage?.outputTokens ?: 0
                }
                else -> Unit
            }
        }

        /** Build the final [AssistantTurn] once [SseStreamEvent.MessageStop] arrives. */
        fun build(): AssistantTurn {
            val texts = textBuilders.entries
                .sortedBy { it.key }
                .map { it.value.toString() }
                .filter { it.isNotBlank() }

            val toolUses = toolBuilders.entries
                .sortedBy { it.key }
                .mapNotNull { (_, builder) -> builder.build(json) }

            return AssistantTurn(
                textBlocks    = texts,
                toolUseBlocks = toolUses,
                stopReason    = stopReason,
                inputTokens   = inputTokens,
                outputTokens  = outputTokens,
            )
        }
    }

    private class ToolBlockBuilder(val id: String, val name: String) {
        private val jsonBuf = StringBuilder()
        fun append(partial: String) { jsonBuf.append(partial) }
        fun build(json: Json): ToolUseBlock? = try {
            val obj = json.parseToJsonElement(jsonBuf.toString()).jsonObject
            ToolUseBlock(id = id, name = name, input = obj)
        } catch (e: Exception) {
            Log.w("ClaudeApiService", "Failed to parse tool input for $name: ${e.message}")
            null
        }
    }

    companion object {
        private const val TAG              = "ClaudeApiService"
        private const val DEFAULT_MODEL    = "claude-sonnet-4-6"
        private const val DEFAULT_MAX_TOKENS = 8096
        private val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()
    }
}

// ── Domain types ──────────────────────────────────────────────────────────────

/**
 * A complete assistant turn accumulated from the streaming response.
 *
 * @param textBlocks     Ordered list of text segments (typically one, but may be
 *                       multiple if interleaved with tool_use).
 * @param toolUseBlocks  Tool calls the model wants to make (may be empty).
 * @param stopReason     "end_turn" | "tool_use" | "max_tokens" | null.
 */
data class AssistantTurn(
    val textBlocks:    List<String>,
    val toolUseBlocks: List<ToolUseBlock>,
    val stopReason:    String?,
    val inputTokens:   Int = 0,
    val outputTokens:  Int = 0,
) {
    val hasToolUse: Boolean get() = toolUseBlocks.isNotEmpty()
    val fullText:   String  get() = textBlocks.joinToString("")
}
