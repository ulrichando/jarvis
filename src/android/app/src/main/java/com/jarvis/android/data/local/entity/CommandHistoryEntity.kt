package com.jarvis.android.data.local.entity

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

/**
 * A terminal command that was executed in a PTY session.
 *
 * Recorded by [TerminalRepositoryImpl] whenever text ending with `\n` is
 * written to a session. Powers the up-arrow command history in [TerminalView]
 * and the command palette in the chat input bar.
 *
 * The table keeps a rolling window of [HISTORY_LIMIT] rows; older entries
 * are pruned automatically by [CommandHistoryDao.pruneOldest].
 */
@Entity(
    tableName = "command_history",
    indices = [Index("executed_at"), Index("session_id")],
)
data class CommandHistoryEntity(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,

    /** The full command string as typed (including pipes, redirects, etc.). */
    val command: String,

    /** PTY session ID that ran this command (from [ActiveSession.id]). */
    @ColumnInfo(name = "session_id")
    val sessionId: String,

    /** Process exit code. -1 = still running or unknown. */
    @ColumnInfo(name = "exit_code")
    val exitCode: Int = -1,

    /** Unix epoch millis when the command was sent to the PTY. */
    @ColumnInfo(name = "executed_at")
    val executedAt: Long = System.currentTimeMillis(),

    /** Wall-clock duration of the command in milliseconds. 0 if not measured. */
    @ColumnInfo(name = "duration_ms")
    val durationMs: Long = 0,

    /** True if this command was injected by the AI agent rather than typed by the user. */
    @ColumnInfo(name = "from_agent")
    val fromAgent: Boolean = false,
) {
    companion object {
        /** Maximum rows kept in the table before oldest entries are pruned. */
        const val HISTORY_LIMIT = 1000
    }
}
