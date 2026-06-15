package com.jarvis.android.data.api

import android.util.Log
import com.jarvis.android.core.network.RawSseEvent
import com.jarvis.android.data.api.dto.ApiError
import com.jarvis.android.data.api.dto.ContentBlockDeltaEnvelope
import com.jarvis.android.data.api.dto.ContentBlockStartEnvelope
import com.jarvis.android.data.api.dto.ContentBlockStopEnvelope
import com.jarvis.android.data.api.dto.ContentDelta
import com.jarvis.android.data.api.dto.ErrorEnvelope
import com.jarvis.android.data.api.dto.MessageDeltaEnvelope
import com.jarvis.android.data.api.dto.MessageStartEnvelope
import com.jarvis.android.data.api.dto.SseStreamEvent
import kotlinx.serialization.json.Json
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Converts [RawSseEvent.Message] frames from [SseClient] into typed [SseStreamEvent]s.
 *
 * The Claude streaming API sends events in the form:
 * ```
 * event: content_block_delta
 * data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}
 * ```
 *
 * [parse] is stateless — each call translates exactly one raw frame. Unknown event
 * types are silently dropped so future API additions don't break the client.
 */
@Singleton
class SseParser @Inject constructor() {

    private val json = Json {
        ignoreUnknownKeys = true
        isLenient         = true
    }

    /**
     * Parse one [RawSseEvent.Message] into a [SseStreamEvent].
     *
     * Returns null for event types that do not require any action
     * (e.g. unrecognised future events).
     */
    fun parse(event: RawSseEvent.Message): SseStreamEvent? {
        val data = event.data ?: return null

        return try {
            when (event.type) {
                "message_start" -> {
                    val env = json.decodeFromString<MessageStartEnvelope>(data)
                    SseStreamEvent.MessageStart(env.message)
                }

                "content_block_start" -> {
                    val env = json.decodeFromString<ContentBlockStartEnvelope>(data)
                    SseStreamEvent.ContentBlockStart(env.index, env.contentBlock)
                }

                "content_block_delta" -> {
                    val env = json.decodeFromString<ContentBlockDeltaEnvelope>(data)
                    val delta: ContentDelta = when (env.delta.type) {
                        "text_delta"       -> ContentDelta.TextDelta(env.delta.text ?: "")
                        "input_json_delta" -> ContentDelta.InputJsonDelta(env.delta.partialJson ?: "")
                        else               -> return null
                    }
                    SseStreamEvent.ContentBlockDelta(env.index, delta)
                }

                "content_block_stop" -> {
                    val env = json.decodeFromString<ContentBlockStopEnvelope>(data)
                    SseStreamEvent.ContentBlockStop(env.index)
                }

                "message_delta" -> {
                    val env = json.decodeFromString<MessageDeltaEnvelope>(data)
                    SseStreamEvent.MessageDelta(env.delta, env.usage)
                }

                "message_stop" -> SseStreamEvent.MessageStop

                "ping"         -> SseStreamEvent.Ping

                "error" -> {
                    val env = json.decodeFromString<ErrorEnvelope>(data)
                    SseStreamEvent.StreamError(env.error)
                }

                else -> {
                    Log.d(TAG, "Unknown SSE event type: ${event.type}")
                    null
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to parse SSE event type=${event.type}: ${e.message}")
            SseStreamEvent.StreamError(ApiError(type = "parse_error", message = e.message ?: "unknown"))
        }
    }

    private companion object {
        const val TAG = "SseParser"
    }
}
