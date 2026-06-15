package com.jarvis.android.data.local.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import com.jarvis.android.data.local.entity.MessageEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface MessageDao {

    // ── Write ─────────────────────────────────────────────────────────────

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(message: MessageEntity): Long

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertAll(messages: List<MessageEntity>)

    // ── Read ──────────────────────────────────────────────────────────────

    /**
     * All messages for [conversationId] in chronological order.
     * Observed as a [Flow] so the chat screen reacts to new inserts
     * (including streaming assistant messages) automatically.
     */
    @Query("""
        SELECT * FROM messages
        WHERE conversation_id = :conversationId
        ORDER BY timestamp ASC, id ASC
    """)
    fun observeByConversation(conversationId: String): Flow<List<MessageEntity>>

    /**
     * One-shot load of the most recent [limit] messages in a conversation,
     * in chronological order. Used to build the context window for the next
     * API request without loading the entire history.
     */
    @Query("""
        SELECT * FROM (
            SELECT * FROM messages
            WHERE conversation_id = :conversationId
            ORDER BY timestamp DESC, id DESC
            LIMIT :limit
        ) ORDER BY timestamp ASC, id ASC
    """)
    suspend fun getRecentByConversation(
        conversationId: String,
        limit: Int = 40,
    ): List<MessageEntity>

    /** Fetch a single message by its auto-generated [id]. */
    @Query("SELECT * FROM messages WHERE id = :id")
    suspend fun getById(id: Long): MessageEntity?

    /** Count messages in a conversation (fast — no column read). */
    @Query("SELECT COUNT(*) FROM messages WHERE conversation_id = :conversationId")
    suspend fun countByConversation(conversationId: String): Int

    // ── Update ────────────────────────────────────────────────────────────

    /**
     * Append streaming text to an existing assistant message.
     * Called repeatedly during a streaming turn before the final
     * [insert] with the complete content.
     */
    @Query("UPDATE messages SET content = content || :chunk WHERE id = :id")
    suspend fun appendContent(id: Long, chunk: String)

    /** Set the stop_reason once streaming completes. */
    @Query("UPDATE messages SET stop_reason = :reason WHERE id = :id")
    suspend fun setStopReason(id: Long, reason: String?)

    // ── Delete ────────────────────────────────────────────────────────────

    /** Delete all messages for a conversation (e.g. "Clear chat"). */
    @Query("DELETE FROM messages WHERE conversation_id = :conversationId")
    suspend fun deleteByConversation(conversationId: String)

    /** Delete messages older than [beforeTimestamp] for pruning long-running conversations. */
    @Query("""
        DELETE FROM messages
        WHERE conversation_id = :conversationId AND timestamp < :beforeTimestamp
    """)
    suspend fun deleteBefore(conversationId: String, beforeTimestamp: Long)

    /** Drop a single message by rowid — used by the "regenerate" action in
     *  the chat UI so we can remove the last assistant turn before replaying
     *  the user's prompt. */
    @Query("DELETE FROM messages WHERE id = :id")
    suspend fun deleteById(id: Long)
}
