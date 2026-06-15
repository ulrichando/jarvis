package com.jarvis.android.data.local.entity

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

/**
 * A named conversation thread — the top-level container for [MessageEntity] rows.
 *
 * Schema notes:
 *   - [id] is a UUID string, generated at creation time.
 *   - [updatedAt] is bumped on every appended message so the conversation
 *     list can be sorted by recency efficiently via the index.
 *   - [isPinned] keeps important conversations at the top of the drawer.
 *   - Token tallies are accumulated incrementally so the UI can show cost
 *     without summing the whole message table.
 */
@Entity(
    tableName = "conversations",
    indices = [Index("updated_at"), Index("is_pinned")],
)
data class ConversationEntity(
    @PrimaryKey
    val id: String,

    /** Human-readable title (first user message, truncated, or user-renamed). */
    val title: String,

    /** Model ID used in this conversation, e.g. `"claude-sonnet-4-6"`. */
    val model: String,

    /** Unix epoch millis when the conversation was created. */
    @ColumnInfo(name = "created_at")
    val createdAt: Long = System.currentTimeMillis(),

    /** Unix epoch millis when the last message was appended. */
    @ColumnInfo(name = "updated_at")
    val updatedAt: Long = System.currentTimeMillis(),

    /** Number of messages; maintained by the repository for quick display. */
    @ColumnInfo(name = "message_count")
    val messageCount: Int = 0,

    /** Cumulative input tokens across all turns in this conversation. */
    @ColumnInfo(name = "total_input_tokens")
    val totalInputTokens: Int = 0,

    /** Cumulative output tokens across all turns. */
    @ColumnInfo(name = "total_output_tokens")
    val totalOutputTokens: Int = 0,

    /** True if the user has pinned this conversation to the top of the list. */
    @ColumnInfo(name = "is_pinned")
    val isPinned: Boolean = false,
)
