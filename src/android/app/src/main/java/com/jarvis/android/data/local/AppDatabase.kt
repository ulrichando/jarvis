package com.jarvis.android.data.local

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import com.jarvis.android.data.local.dao.CommandHistoryDao
import com.jarvis.android.data.local.dao.ConversationDao
import com.jarvis.android.data.local.dao.MessageDao
import com.jarvis.android.data.local.dao.ModelDao
import com.jarvis.android.data.local.entity.CommandHistoryEntity
import com.jarvis.android.data.local.entity.ConversationEntity
import com.jarvis.android.data.local.entity.MessageEntity
import com.jarvis.android.data.local.entity.ModelEntity

/**
 * Single Room database for JARVIS Android.
 *
 * Tables:
 *   - `conversations`  — named chat threads ([ConversationEntity])
 *   - `messages`       — individual turns within a thread ([MessageEntity])
 *   - `command_history`— PTY command log ([CommandHistoryEntity])
 *   - `local_models`   — on-device LLM catalog + download state ([ModelEntity])
 *
 * Version history:
 *   1 → conversations, messages, command_history
 *   2 → added local_models table (Module A)
 */
@Database(
    entities = [
        ConversationEntity::class,
        MessageEntity::class,
        CommandHistoryEntity::class,
        ModelEntity::class,
    ],
    version = AppDatabase.DATABASE_VERSION,
    exportSchema = true,
)
abstract class AppDatabase : RoomDatabase() {

    abstract fun conversationDao(): ConversationDao
    abstract fun messageDao(): MessageDao
    abstract fun commandHistoryDao(): CommandHistoryDao
    abstract fun modelDao(): ModelDao

    companion object {
        const val DATABASE_VERSION = 2
        const val DATABASE_NAME    = "jarvis.db"

        /** All migrations in ascending version order. */
        val MIGRATIONS: Array<Migration> = arrayOf(MIGRATION_1_2)

        fun create(context: Context): AppDatabase =
            Room.databaseBuilder(context, AppDatabase::class.java, DATABASE_NAME)
                .addCallback(JarvisCallback())
                .addMigrations(*MIGRATIONS)
                .setJournalMode(JournalMode.WRITE_AHEAD_LOGGING)
                .build()
    }
}

// ── Migrations ────────────────────────────────────────────────────────────────

/**
 * v1 → v2: Add the `local_models` table introduced by Module A (Local LLM Engine).
 *
 * All columns have defaults so no backfill is needed on existing installs.
 * The catalog is seeded by [ModelRegistry] on first access after upgrade.
 */
private val MIGRATION_1_2 = object : Migration(1, 2) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("""
            CREATE TABLE IF NOT EXISTS `local_models` (
                `id`               TEXT    NOT NULL PRIMARY KEY,
                `name`             TEXT    NOT NULL,
                `family`           TEXT    NOT NULL,
                `param_count`      TEXT    NOT NULL,
                `quantization`     TEXT    NOT NULL,
                `size_bytes`       INTEGER NOT NULL,
                `ram_required_mb`  INTEGER NOT NULL,
                `backend`          TEXT    NOT NULL,
                `download_url`     TEXT    NOT NULL,
                `sha256`           TEXT    NOT NULL DEFAULT '',
                `mirror_urls`      TEXT    NOT NULL DEFAULT '',
                `capabilities`     TEXT    NOT NULL DEFAULT 'CHAT',
                `context_length`   INTEGER NOT NULL DEFAULT 2048,
                `license`          TEXT    NOT NULL DEFAULT '',
                `description`      TEXT    NOT NULL DEFAULT '',
                `download_state`   TEXT    NOT NULL DEFAULT 'NOT_DOWNLOADED',
                `local_path`       TEXT,
                `is_custom`        INTEGER NOT NULL DEFAULT 0,
                `updated_at`       INTEGER NOT NULL DEFAULT 0
            )
        """.trimIndent())

        db.execSQL("CREATE INDEX IF NOT EXISTS `index_local_models_backend`        ON `local_models` (`backend`)")
        db.execSQL("CREATE INDEX IF NOT EXISTS `index_local_models_family`         ON `local_models` (`family`)")
        db.execSQL("CREATE INDEX IF NOT EXISTS `index_local_models_download_state` ON `local_models` (`download_state`)")
        db.execSQL("CREATE INDEX IF NOT EXISTS `index_local_models_is_custom`      ON `local_models` (`is_custom`)")
    }
}

// ── DB open callback ──────────────────────────────────────────────────────────

private class JarvisCallback : RoomDatabase.Callback() {
    override fun onOpen(db: SupportSQLiteDatabase) {
        super.onOpen(db)
        db.execSQL("PRAGMA foreign_keys = ON")
    }
}
