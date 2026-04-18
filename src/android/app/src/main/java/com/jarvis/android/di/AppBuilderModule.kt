package com.jarvis.android.di

import com.jarvis.android.data.repository.AppBuilderRepositoryImpl
import com.jarvis.android.domain.repository.AppBuilderRepository
import dagger.Binds
import dagger.Module
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

/**
 * Hilt module for Module B — Mobile App Builder.
 *
 * [AppTemplateRegistry], [AppCodeGenerator], and [AppBuildEngine] are all
 * annotated with `@Singleton` and `@Inject constructor`, so Hilt wires them
 * automatically. Only the repository interface→impl binding is needed here.
 */
@Module
@InstallIn(SingletonComponent::class)
abstract class AppBuilderModule {

    @Binds
    @Singleton
    abstract fun bindAppBuilderRepository(
        impl: AppBuilderRepositoryImpl,
    ): AppBuilderRepository
}
