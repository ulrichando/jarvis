package com.jarvis.android.data.local.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import com.jarvis.android.data.local.entity.CommandHistoryEntity
import kotlinx.coroutines.flow.Flow

@Dao
interface CommandHistoryDao {

    // ── Write ─────────────────────────────────────────────────────────────

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(entry: CommandHistoryEntity): Long

    /** Update the exit code and duration once the command completes. */
    @Query("""
        UPDATE command_history
        SET exit_code = :exitCode, duration_ms = :durationMs
        WHERE id = :id
    """)
    suspend fun updateResult(id: Long, exitCode: Int, durationMs: Long)

    // ── Read ──────────────────────────────────────────────────────────────

    /**
     * Most recent [limit] commands across all sessions, newest first.
     * Used by the chat input bar command palette.
     */
    @Query("""
        SELECT * FROM command_history
        ORDER BY executed_at DESC
        LIMIT :limit
    """)
    suspend fun getRecent(limit: Int = 50): List<CommandHistoryEntity>

    /**
     * Commands for a specific PTY session, newest first.
     * Used by [TerminalView] for up-arrow history navigation.
     */
    @Query("""
        SELECT * FROM command_history
        WHERE session_id = :sessionId
        ORDER BY executed_at DESC
        LIMIT :limit
    """)
    suspend fun getBySession(sessionId: String, limit: Int = 200): List<CommandHistoryEntity>

    /**
     * Observe the live command history as a [Flow].
     * Consumed by [TerminalViewModel] to update the suggestion strip in real time.
     */
    @Query("""
        SELECT * FROM command_history
        WHERE session_id = :sessionId
        ORDER BY executed_at DESC
        LIMIT :limit
    """)
    fun observeBySession(sessionId: String, limit: Int = 50): Flow<List<CommandHistoryEntity>>

    /**
     * Deduplicated list of unique command strings for autocomplete.
     * Ordered by recency — the most recently used variant of each command appears first.
     */
    @Query("""
        SELECT command FROM command_history
        WHERE command LIKE :prefix || '%'
        GROUP BY command
        ORDER BY MAX(executed_at) DESC
        LIMIT :limit
    """)
    suspend fun searchByPrefix(prefix: String, limit: Int = 20): List<String>

    // ── Pruning ───────────────────────────────────────────────────────────

    /**
     * Delete the oldest entries so the table never exceeds [CommandHistoryEntity.HISTORY_LIMIT].
     * Call after every insert.
     */
    @Query("""
        DELETE FROM command_history
        WHERE id NOT IN (
            SELECT id FROM command_history
            ORDER BY executed_at DESC
            LIMIT ${CommandHistoryEntity.HISTORY_LIMIT}
        )
    """)
    suspend fun pruneOldest()

    @Query("DELETE FROM command_history WHERE session_id = :sessionId")
    suspend fun deleteBySession(sessionId: String)

    @Query("DELETE FROM command_history")
    suspend fun deleteAll()
}
