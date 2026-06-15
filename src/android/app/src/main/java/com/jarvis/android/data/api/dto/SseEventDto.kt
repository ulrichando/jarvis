package com.jarvis.android.data.api.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

// ── SSE stream events (Claude API) ────────────────────────────────────────────
//
// Each SSE frame has:
//   event: <type>
//   data:  <json>
//
// The full sequence for a streaming response with a tool_use block:
//
//   message_start
//   content_block_start   (index=0, type="text")
//   content_block_delta   (index=0, delta.type="text_delta")
//   ...
//   content_block_stop    (index=0)
//   content_block_start   (index=1, type="tool_use")
//   content_block_delta   (index=1, delta.type="input_json_delta")
//   ...
//   content_block_stop    (index=1)
//   message_delta         (stop_reason="tool_use")
//   message_stop

/** Typed representation of one SSE data payload from the Claude streaming API. */
sealed class SseStreamEvent {

    /** First event — carries the initial message metadata. */
    data class MessageStart(
        val message: MessageStartData,
    ) : SseStreamEvent()

    /** A new content block is opening at [index]. */
    data class ContentBlockStart(
        val index: Int,
        val contentBlock: ContentBlockStartData,
    ) : SseStreamEvent()

    /** Incremental data for the content block at [index]. */
    data class ContentBlockDelta(
        val index: Int,
        val delta: ContentDelta,
    ) : SseStreamEvent()

    /** Content block at [index] is complete. */
    data class ContentBlockStop(val index: Int) : SseStreamEvent()

    /** Message-level metadata update (stop_reason, usage). */
    data class MessageDelta(
        val delta:   MessageDeltaData,
        val usage:   DeltaUsage?,
    ) : SseStreamEvent()

    /** Stream is fully complete. */
    object MessageStop : SseStreamEvent()

    /** Server keepalive — ignore. */
    object Ping : SseStreamEvent()

    /** The API returned an error object inside the stream. */
    data class StreamError(
        val error: ApiError,
    ) : SseStreamEvent()
}

// ── Payload data classes ──────────────────────────────────────────────────────

@Serializable
data class MessageStartData(
    val id:    String,
    val type:  String,
    val role:  String,
    val model: String,
    val usage: TokenUsage?,
)

@Serializable
data class TokenUsage(
    @SerialName("input_tokens")  val inputTokens:  Int,
    @SerialName("output_tokens") val outputTokens: Int,
)

@Serializable
data class DeltaUsage(
    @SerialName("output_tokens") val outputTokens: Int,
)

/** Describes the type of content block that is about to be streamed. */
@Serializable
data class ContentBlockStartData(
    val type:  String,              // "text" | "tool_use"
    val id:    String? = null,      // present for tool_use
    val name:  String? = null,      // present for tool_use
)

/** Incremental content — either a text chunk or a JSON fragment for tool input. */
sealed class ContentDelta {
    /** Incremental text characters. */
    data class TextDelta(val text: String) : ContentDelta()

    /**
     * Incremental JSON string fragment for a `tool_use` input object.
     * Accumulate all fragments and parse the complete JSON when the block closes.
     */
    data class InputJsonDelta(val partialJson: String) : ContentDelta()
}

@Serializable
data class MessageDeltaData(
    @SerialName("stop_reason")   val stopReason:   String?,   // "end_turn" | "tool_use" | "max_tokens"
    @SerialName("stop_sequence") val stopSequence: String?,
)

@Serializable
data class ApiError(
    val type:    String,
    val message: String,
)

// ── Raw JSON wrappers (used by SseParser) ─────────────────────────────────────

/** Raw JSON envelope for `message_start`. */
@Serializable
internal data class MessageStartEnvelope(
    val message: MessageStartData,
)

/** Raw JSON envelope for `content_block_start`. */
@Serializable
internal data class ContentBlockStartEnvelope(
    val index:           Int,
    @SerialName("content_block") val contentBlock: ContentBlockStartData,
)

/** Raw JSON envelope for `content_block_delta`. */
@Serializable
internal data class ContentBlockDeltaEnvelope(
    val index: Int,
    val delta: ContentDeltaRaw,
)

/** Raw JSON envelope for `content_block_stop`. */
@Serializable
internal data class ContentBlockStopEnvelope(
    val index: Int,
)

/** Raw JSON envelope for `message_delta`. */
@Serializable
internal data class MessageDeltaEnvelope(
    val delta: MessageDeltaData,
    val usage: DeltaUsage? = null,
)

/** Raw JSON envelope for `error`. */
@Serializable
internal data class ErrorEnvelope(
    val error: ApiError,
)

/** Intermediate delta type before we discriminate text vs input_json. */
@Serializable
internal data class ContentDeltaRaw(
    val type:         String,
    val text:         String? = null,
    @SerialName("partial_json") val partialJson: String? = null,
)
