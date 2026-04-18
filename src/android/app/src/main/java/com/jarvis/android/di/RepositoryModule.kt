package com.jarvis.android.di

import com.jarvis.android.core.network.ApiKeyProvider
import com.jarvis.android.data.repository.ApiKeyProviderImpl
import com.jarvis.android.data.repository.ChatRepositoryImpl
import com.jarvis.android.data.repository.FileRepositoryImpl
import com.jarvis.android.data.repository.ModelDownloaderService
import com.jarvis.android.data.repository.ModelRegistrySource
import com.jarvis.android.data.repository.ModelRepositoryImpl
import com.jarvis.android.data.repository.SensorRepositoryImpl
import com.jarvis.android.data.repository.SystemRepositoryImpl
import com.jarvis.android.data.repository.TerminalRepositoryImpl
import com.jarvis.android.domain.repository.ChatRepository
import com.jarvis.android.domain.repository.FileRepository
import com.jarvis.android.domain.repository.ModelRepository
import com.jarvis.android.domain.repository.SensorRepository
import com.jarvis.android.domain.repository.SystemRepository
import com.jarvis.android.domain.repository.TerminalRepository
import com.jarvis.android.system.llm.ModelDownloader
import com.jarvis.android.system.llm.ModelRegistry
import dagger.Binds
import dagger.Module
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

/**
 * Binds domain repository interfaces to their data-layer implementations.
 * All are singletons — repositories hold no per-screen state.
 */
@Module
@InstallIn(SingletonComponent::class)
abstract class RepositoryModule {

    @Binds @Singleton
    abstract fun bindChatRepository(impl: ChatRepositoryImpl): ChatRepository

    @Binds @Singleton
    abstract fun bindSystemRepository(impl: SystemRepositoryImpl): SystemRepository

    @Binds @Singleton
    abstract fun bindFileRepository(impl: FileRepositoryImpl): FileRepository

    @Binds @Singleton
    abstract fun bindTerminalRepository(impl: TerminalRepositoryImpl): TerminalRepository

    @Binds @Singleton
    abstract fun bindSensorRepository(impl: SensorRepositoryImpl): SensorRepository

    /** [ApiKeyProviderImpl] implements both [ApiKeyProvider] (for OkHttp interceptor)
     *  and its own public API (for Settings screen). Bind the interface here. */
    @Binds @Singleton
    abstract fun bindApiKeyProvider(impl: ApiKeyProviderImpl): ApiKeyProvider

    // ── Local LLM ─────────────────────────────────────────────────────────────

    @Binds @Singleton
    abstract fun bindModelRepository(impl: ModelRepositoryImpl): ModelRepository

    @Binds @Singleton
    abstract fun bindModelDownloaderService(impl: ModelDownloader): ModelDownloaderService

    @Binds @Singleton
    abstract fun bindModelRegistrySource(impl: ModelRegistry): ModelRegistrySource
}
