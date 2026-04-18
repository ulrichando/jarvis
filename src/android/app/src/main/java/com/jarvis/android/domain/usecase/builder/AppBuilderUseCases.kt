package com.jarvis.android.domain.usecase.builder

import com.jarvis.android.domain.model.AppProject
import com.jarvis.android.domain.model.AppTemplate
import com.jarvis.android.domain.model.AppType
import com.jarvis.android.domain.model.BuildResult
import com.jarvis.android.domain.model.GenerationRequest
import com.jarvis.android.domain.model.TemplateCategory
import com.jarvis.android.domain.repository.AppBuilderRepository
import kotlinx.coroutines.flow.Flow
import javax.inject.Inject

// ── Templates ─────────────────────────────────────────────────────────────────

class GetTemplatesUseCase @Inject constructor(private val repo: AppBuilderRepository) {
    operator fun invoke(): List<AppTemplate> = repo.getTemplates()
}

class GetTemplatesByCategoryUseCase @Inject constructor(private val repo: AppBuilderRepository) {
    operator fun invoke(category: TemplateCategory): List<AppTemplate> =
        repo.getTemplatesByCategory(category)
}

// ── Projects ──────────────────────────────────────────────────────────────────

class ObserveProjectsUseCase @Inject constructor(private val repo: AppBuilderRepository) {
    operator fun invoke(): Flow<List<AppProject>> = repo.observeProjects()
}

class CreateProjectUseCase @Inject constructor(private val repo: AppBuilderRepository) {
    suspend operator fun invoke(
        name:        String,
        description: String,
        type:        AppType,
        templateId:  String? = null,
    ): AppProject = repo.createProject(name, description, type, templateId)
}

class UpdateSourceCodeUseCase @Inject constructor(private val repo: AppBuilderRepository) {
    suspend operator fun invoke(projectId: String, code: String) =
        repo.updateSourceCode(projectId, code)
}

class RenameProjectUseCase @Inject constructor(private val repo: AppBuilderRepository) {
    suspend operator fun invoke(projectId: String, name: String) =
        repo.renameProject(projectId, name)
}

class DeleteProjectUseCase @Inject constructor(private val repo: AppBuilderRepository) {
    suspend operator fun invoke(projectId: String) = repo.deleteProject(projectId)
}

// ── Generation ────────────────────────────────────────────────────────────────

/**
 * Stream AI-generated code for a project.
 *
 * Emits status strings while running. The final meaningful emission is one of:
 *   - `"CODE:<source>"` — successfully generated source code
 *   - `"CLOUD_PROMPT:<prompt>"` — cloud routing selected; paste into Chat tab
 *   - `"ERROR:<message>"` — generation failed
 */
class GenerateAppCodeUseCase @Inject constructor(private val repo: AppBuilderRepository) {
    operator fun invoke(request: GenerationRequest): Flow<String> =
        repo.generateCode(request)
}

// ── Build & run ───────────────────────────────────────────────────────────────

class BuildProjectUseCase @Inject constructor(private val repo: AppBuilderRepository) {
    suspend operator fun invoke(projectId: String): BuildResult = repo.buildProject(projectId)
}

class GetLaunchPathUseCase @Inject constructor(private val repo: AppBuilderRepository) {
    suspend operator fun invoke(projectId: String): String? = repo.getLaunchPath(projectId)
}
