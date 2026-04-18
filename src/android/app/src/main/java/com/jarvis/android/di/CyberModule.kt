package com.jarvis.android.di

import com.jarvis.android.data.repository.CyberRepositoryImpl
import com.jarvis.android.domain.repository.CyberRepository
import dagger.Binds
import dagger.Module
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

/**
 * Hilt module for Module C — Cybersecurity Suite.
 *
 * [PortScanner], [HttpInspector], [ProcessWatcher], [NetworkMonitor], and
 * [LogWatcher] are all `@Singleton @Inject constructor` — auto-wired by Hilt.
 * Only the repository interface→impl binding is declared here.
 */
@Module
@InstallIn(SingletonComponent::class)
abstract class CyberModule {

    @Binds
    @Singleton
    abstract fun bindCyberRepository(impl: CyberRepositoryImpl): CyberRepository
}
