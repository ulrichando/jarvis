package com.jarvis.android.data.api.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject

// ── Request body ──────────────────────────────────────────────────────────────

/**
 * Top-level request body sent to `POST /v1/messages`.
 *
 * @param model       Model ID, e.g. `"claude-sonnet-4-6"`.
 * @param maxTokens   Hard token cap on the response.
 * @param system      Optional system prompt injected before the conversation.
 * @param messages    The conversation history.
 * @param tools       Tool definitions exposed to the model.
 * @param toolChoice  How the model chooses tools (`auto` / `any` / specific).
 * @param stream      True enables server-sent event streaming.
 * @param temperature Sampling temperature [0.0, 1.0].
 */
@Serializable
data class MessageRequestDto(
    val model: String,
    @SerialName("max_tokens") val maxTokens: Int,
    val system: String? = null,
    val messages: List<MessageDto>,
    val tools: List<ToolDefinitionDto> = emptyList(),
    @SerialName("tool_choice") val toolChoice: ToolChoiceDto? = null,
    val stream: Boolean = true,
    val temperature: Double = 0.7,
)

// ── Message ───────────────────────────────────────────────────────────────────

/**
 * A single turn in the conversation.
 *
 * `content` is polymorphic:
 *   - `String`                        → plain text (user shorthand; API accepts it)
 *   - `List<ContentBlockDto>`         → rich content (tool_use / tool_result / image)
 *
 * We always serialise as a list so the DTO is consistent.
 */
@Serializable
data class MessageDto(
    val role: String,                      // "user" | "assistant"
    val content: List<ContentBlockDto>,
)

// ── Content blocks ────────────────────────────────────────────────────────────

/**
 * Sealed hierarchy covering every content block type the Claude API accepts.
 *
 * Serialisation discriminator: the `type` field.
 */
@Serializable
sealed class ContentBlockDto {

    /** Plain text block. Used in both user and assistant turns. */
    @Serializable
    @SerialName("text")
    data class Text(
        val text: String,
    ) : ContentBlockDto()

    /**
     * A tool invocation the *model* decided to make.
     * Appears in assistant-role messages only.
     */
    @Serializable
    @SerialName("tool_use")
    data class ToolUse(
        val id:    String,
        val name:  String,
        val input: JsonObject,
    ) : ContentBlockDto()

    /**
     * The result of a tool invocation, returned by the app.
     * Appears in user-role messages that follow an assistant tool_use turn.
     *
     * @param isError  Set to true when the tool execution failed.
     */
    @Serializable
    @SerialName("tool_result")
    data class ToolResult(
        @SerialName("tool_use_id") val toolUseId: String,
        val content: String,
        @SerialName("is_error") val isError: Boolean = false,
    ) : ContentBlockDto()

    /**
     * A base64-encoded image (camera frame, screenshot) to feed the model.
     */
    @Serializable
    @SerialName("image")
    data class Image(
        val source: ImageSource,
    ) : ContentBlockDto()
}

@Serializable
data class ImageSource(
    val type:       String,              // "base64"
    @SerialName("media_type") val mediaType: String,   // "image/jpeg" | "image/png"
    val data:       String,              // base64-encoded bytes
)

// ── Tool definition ───────────────────────────────────────────────────────────

/**
 * Declares a tool to the model.
 *
 * @param name         Snake-case tool name (must match [JarvisToolDispatcher] routing).
 * @param description  One-paragraph description used by the model to decide when to call it.
 * @param inputSchema  JSON Schema object describing the tool's parameters.
 */
@Serializable
data class ToolDefinitionDto(
    val name:        String,
    val description: String,
    @SerialName("input_schema") val inputSchema: JsonObject,
)

// ── Tool choice ───────────────────────────────────────────────────────────────

/** Controls when the model uses tools. `auto` = model decides (default). */
@Serializable
data class ToolChoiceDto(
    val type: String,                    // "auto" | "any" | "tool"
    val name: String? = null,            // required when type == "tool"
)
