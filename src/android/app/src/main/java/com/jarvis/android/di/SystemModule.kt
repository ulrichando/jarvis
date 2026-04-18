package com.jarvis.android.di

import android.content.Context
import com.jarvis.android.domain.repository.ModelRepository
import com.jarvis.android.system.adb.AdbManager
import com.jarvis.android.system.root.RootManager
import com.jarvis.android.system.root.RootServiceConnection
import com.jarvis.android.system.root.RootShell
import com.jarvis.android.system.terminal.PtyManager
import com.jarvis.android.system.terminal.TerminalSessionManager
import com.jarvis.android.system.tools.JarvisToolDispatcher
import com.jarvis.android.system.permissions.PermissionManager
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import javax.inject.Qualifier
import javax.inject.Singleton

/**
 * Provides system-level singletons: root management, PTY, terminal sessions,
 * tool dispatcher, permission manager, and the application-scoped coroutine scope.
 *
 * Most of these classes already use `@Inject constructor` so Hilt can build
 * them automatically. This module only provides the few that need explicit
 * factory logic or qualifier annotations.
 */
@Module
@InstallIn(SingletonComponent::class)
object SystemModule {

    /**
     * Application-wide [CoroutineScope] for fire-and-forget work that must
     * outlive any individual ViewModel (e.g. recording command history,
     * persisting tool results).
     *
     * Uses [SupervisorJob] so a child failure doesn't cancel siblings.
     * Cancelled when the process dies — never manually.
     */
    @Provides
    @Singleton
    @ApplicationScope
    fun provideApplicationScope(): CoroutineScope =
        CoroutineScope(SupervisorJob() + Dispatchers.Default)

    /**
     * [RootManager] initialises libsu shell flags on first use.
     * Provided here so it is a true singleton and `configure()` is called
     * exactly once before any root operation.
     */
    @Provides
    @Singleton
    fun provideRootManager(): RootManager =
        RootManager().also { it.configure() }

    @Provides @Singleton
    fun provideRootShell(rootManager: RootManager): RootShell = RootShell(rootManager)

    @Provides @Singleton
    fun providePtyManager(): PtyManager = PtyManager()

    @Provides @Singleton
    fun provideTerminalSessionManager(
        ptyManager: PtyManager,
        rootManager: RootManager,
    ): TerminalSessionManager = TerminalSessionManager(ptyManager, rootManager)

    @Provides @Singleton
    fun provideJarvisToolDispatcher(
        @ApplicationContext context: Context,
        rootShell: RootShell,
        rootManager: RootManager,
        sessionManager: TerminalSessionManager,
        modelRepository: ModelRepository,
    ): JarvisToolDispatcher = JarvisToolDispatcher(context, rootShell, rootManager, sessionManager, modelRepository)

    @Provides @Singleton
    fun providePermissionManager(
        @ApplicationContext context: Context,
        rootManager: RootManager,
        rootShell: RootShell,
        adbManager: AdbManager,
    ): PermissionManager = PermissionManager(context, rootManager, rootShell, adbManager)

    @Provides @Singleton
    fun provideRootServiceConnection(
        @ApplicationContext context: Context,
        rootManager: RootManager,
    ): RootServiceConnection = RootServiceConnection(context, rootManager)
}

/** Qualifier for the application-scoped [CoroutineScope]. */
@Qualifier
@Retention(AnnotationRetention.BINARY)
annotation class ApplicationScope
