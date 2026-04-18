package com.jarvis.android.di

import android.content.Context
import androidx.work.WorkManager
import com.jarvis.android.data.local.AppDatabase
import com.jarvis.android.data.local.dao.CommandHistoryDao
import com.jarvis.android.data.local.dao.ConversationDao
import com.jarvis.android.data.local.dao.MessageDao
import com.jarvis.android.data.local.dao.ModelDao
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object DatabaseModule {

    @Provides
    @Singleton
    fun provideAppDatabase(@ApplicationContext context: Context): AppDatabase =
        AppDatabase.create(context)

    @Provides
    @Singleton
    fun provideConversationDao(db: AppDatabase): ConversationDao = db.conversationDao()

    @Provides
    @Singleton
    fun provideMessageDao(db: AppDatabase): MessageDao = db.messageDao()

    @Provides
    @Singleton
    fun provideCommandHistoryDao(db: AppDatabase): CommandHistoryDao = db.commandHistoryDao()

    @Provides
    @Singleton
    fun provideModelDao(db: AppDatabase): ModelDao = db.modelDao()

    @Provides
    @Singleton
    fun provideWorkManager(@ApplicationContext context: Context): WorkManager =
        WorkManager.getInstance(context)
}
