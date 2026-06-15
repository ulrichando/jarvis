package com.jarvis.android.di

import androidx.work.Configuration
import com.jarvis.android.system.llm.IntelliRouter
import com.jarvis.android.system.llm.LiteRtLmBackend
import com.jarvis.android.system.llm.LlamaJNI
import com.jarvis.android.system.llm.ModelDownloader
import com.jarvis.android.system.llm.ModelRegistry
import dagger.Module
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent

/**
 * Hilt module for the Local LLM subsystem (Module A).
 *
 * ## Component graph
 *
 * All six primary classes are `@Singleton @Inject constructor` — Hilt wires them
 * automatically without explicit `@Provides` methods. This module documents that
 * graph and is the single place to add LLM-specific configuration if needed.
 *
 * ```
 *  ModelRepository (interface)
 *      └─ ModelRepositoryImpl          @Singleton — bound in RepositoryModule
 *           ├─ LlamaJNI                @Singleton — JNI bridge to llama.cpp
 *           ├─ MediaPipeLLM            @Singleton — MediaPipe AI Edge
 *           ├─ ModelDownloader         @Singleton — WorkManager download engine
 *           │    └─ WorkManager        @Singleton — provided in DatabaseModule
 *           ├─ ModelRegistry           @Singleton — built-in catalog
 *           └─ ModelDao                @Singleton — provided in DatabaseModule
 *
 *  IntelliRouter                       @Singleton — routing decision engine
 *       ├─ ModelRepository (above)
 *       └─ ApplicationContext
 *
 *  DownloadWorker                      @HiltWorker — WorkManager worker
 *       └─ ModelDao
 * ```
 *
 * ## WorkManager / HiltWorker
 *
 * [DownloadWorker] uses `@HiltWorker` / `@AssistedInject`. This requires:
 *   1. [JarvisApplication] implementing [Configuration.Provider] so WorkManager
 *      uses [androidx.hilt.work.HiltWorkerFactory] instead of the default factory.
 *   2. The default [androidx.work.WorkManagerInitializer] removed from the manifest
 *      (done in AndroidManifest.xml via `tools:node="remove"`) so WorkManager does
 *      not self-initialize before [JarvisApplication.workManagerConfiguration] runs.
 *
 * ## Routing mode bindings (RepositoryModule)
 *
 * [com.jarvis.android.data.repository.ModelRepositoryImpl] → [com.jarvis.android.domain.repository.ModelRepository]
 * [ModelDownloader]  → [com.jarvis.android.data.repository.ModelDownloaderService]
 * [ModelRegistry]    → [com.jarvis.android.data.repository.ModelRegistrySource]
 */
@Module
@InstallIn(SingletonComponent::class)
object LlmModule
// All bindings are auto-provided (@Singleton @Inject constructor) or live in
// RepositoryModule / DatabaseModule — no explicit @Provides needed here.
