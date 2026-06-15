package com.jarvis.android.data.local.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Update
import com.jarvis.android.data.local.entity.ConversationEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface ConversationDao {

    // ── Write ─────────────────────────────────────────────────────────────

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(conversation: ConversationEntity)

    @Update
    suspend fun update(conversation: ConversationEntity)

    // ── Read ──────────────────────────────────────────────────────────────

    /**
     * All conversations ordered: pinned first, then by most recently updated.
     * Observed as a [Flow] so the drawer reacts to inserts/updates immediately.
     */
    @Query("""
        SELECT * FROM conversations
        ORDER BY is_pinned DESC, updated_at DESC
    """)
    fun observeAll(): Flow<List<ConversationEntity>>

    /** Single conversation by [id]. Returns null if not found. */
    @Query("SELECT * FROM conversations WHERE id = :id")
    suspend fun getById(id: String): ConversationEntity?

    /** Most recent [limit] conversations, regardless of pin state. */
    @Query("SELECT * FROM conversations ORDER BY updated_at DESC LIMIT :limit")
    suspend fun getRecent(limit: Int = 20): List<ConversationEntity>

    // ── Update helpers ────────────────────────────────────────────────────

    /**
     * Bump [updatedAt] and increment the message counter after a new message
     * is appended. Called by [MessageDao] indirectly through the repository.
     */
    @Query("""
        UPDATE conversations
        SET updated_at = :now,
            message_count = message_count + 1,
            total_input_tokens  = total_input_tokens  + :inputTokens,
            total_output_tokens = total_output_tokens + :outputTokens
        WHERE id = :id
    """)
    suspend fun incrementStats(
        id:           String,
        now:          Long = System.currentTimeMillis(),
        inputTokens:  Int  = 0,
        outputTokens: Int  = 0,
    )

    @Query("UPDATE conversations SET title = :title WHERE id = :id")
    suspend fun updateTitle(id: String, title: String)

    @Query("UPDATE conversations SET is_pinned = :pinned WHERE id = :id")
    suspend fun setPinned(id: String, pinned: Boolean)

    // ── Delete ────────────────────────────────────────────────────────────

    /** Cascade-deletes all [MessageEntity] rows for this conversation. */
    @Query("DELETE FROM conversations WHERE id = :id")
    suspend fun deleteById(id: String)

    /** Wipe all conversations and their messages. */
    @Query("DELETE FROM conversations")
    suspend fun deleteAll()
}
