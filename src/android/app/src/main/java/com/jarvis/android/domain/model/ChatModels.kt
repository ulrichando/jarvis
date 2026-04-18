package com.jarvis.android.domain.model

import com.jarvis.android.system.tools.ConfirmationRequest

// ── Conversation ──────────────────────────────────────────────────────────────

data class Conversation(
    val id:                String,
    val title:             String,
    val model:             String,
    val createdAt:         Long,
    val updatedAt:         Long,
    val messageCount:      Int,
    val totalInputTokens:  Int,
    val totalOutputTokens: Int,
    val isPinned:          Boolean,
)

// ── Message ───────────────────────────────────────────────────────────────────

data class Message(
    val id:            Long,
    val conversationId:String,
    val role:          MessageRole,
    val content:       String,
    val contentType:   MessageContentType,
    val toolCallsJson: String?,
    val timestamp:     Long,
    val inputTokens:   Int,
    val outputTokens:  Int,
    val stopReason:    String?,
    val isOffline:     Boolean,
)

enum class MessageRole {
    USER, ASSISTANT;
    fun wire() = name.lowercase()
    companion object { fun from(s: String) = if (s == "user") USER else ASSISTANT }
}

enum class MessageContentType {
    TEXT, TOOL_USE, TOOL_RESULT, IMAGE, MIXED;
    fun wire() = name.lowercase()
}

// ── Streaming events ──────────────────────────────────────────────────────────

/**
 * Events emitted by [ChatRepository.sendMessage] while streaming.
 * Collected by [ChatViewModel] to update [ChatUiState] incrementally.
 */
sealed class ChatEvent {
    /** Incremental text token from the current assistant turn. */
    data class TextDelta(val text: String) : ChatEvent()

    /** The model decided to call a tool. Show a "thinking" indicator. */
    data class ToolCallStarted(
        val toolId:   String,
        val toolName: String,
    ) : ChatEvent()

    /** Tool execution finished. Show collapsed result in the turn. */
    data class ToolCallCompleted(
        val toolId:   String,
        val toolName: String,
        val result:   String,
        val isError:  Boolean,
    ) : ChatEvent()

    /** A dangerous tool requires explicit user approval before proceeding. */
    data class ConfirmationNeeded(val request: ConfirmationRequest) : ChatEvent()

    /** The assistant turn was fully persisted. Contains the saved [messageId]. */
    data class TurnSaved(val messageId: Long) : ChatEvent()

    /** Non-fatal warning (e.g. max tool iterations reached). */
    data class Warning(val message: String) : ChatEvent()

    /** Terminal error — stream will stop after this event. */
    data class Error(val message: String, val isRetryable: Boolean = true) : ChatEvent()

    /** All turns complete, agent loop has exited cleanly. */
    object Done : ChatEvent()
}
