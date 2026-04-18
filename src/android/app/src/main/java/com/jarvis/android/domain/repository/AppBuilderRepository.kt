package com.jarvis.android.domain.repository

import com.jarvis.android.domain.model.AppProject
import com.jarvis.android.domain.model.AppTemplate
import com.jarvis.android.domain.model.AppType
import com.jarvis.android.domain.model.BuildResult
import com.jarvis.android.domain.model.GenerationRequest
import com.jarvis.android.domain.model.TemplateCategory
import kotlinx.coroutines.flow.Flow

/**
 * Repository interface for the JARVIS App Builder (Module B).
 *
 * Abstracts project persistence, AI code generation, and build orchestration
 * behind a clean boundary that ViewModels and use cases interact with.
 */
interface AppBuilderRepository {

    // ── Templates ─────────────────────────────────────────────────────────────

    fun getTemplates(): List<AppTemplate>
    fun getTemplatesByCategory(category: TemplateCategory): List<AppTemplate>
    fun getTemplateById(id: String): AppTemplate?

    // ── Projects ──────────────────────────────────────────────────────────────

    /** Reactive list of all projects, newest first. */
    fun observeProjects(): Flow<List<AppProject>>

    suspend fun getProject(id: String): AppProject?

    /** Create a blank project (no source code yet). */
    suspend fun createProject(
        name:        String,
        description: String,
        type:        AppType,
        templateId:  String? = null,
    ): AppProject

    /** Persist source code edits without triggering a rebuild. */
    suspend fun updateSourceCode(projectId: String, code: String)

    /** Rename a project. */
    suspend fun renameProject(projectId: String, name: String)

    /** Delete project + all built artefacts from disk. */
    suspend fun deleteProject(projectId: String)

    // ── AI generation ─────────────────────────────────────────────────────────

    /**
     * Generate source code for a project via the local LLM or cloud.
     *
     * Emits intermediate status strings while generating, then the final
     * source code as the last emission (prefixed with `"CODE:"`).
     */
    fun generateCode(request: GenerationRequest): Flow<String>

    // ── Build ─────────────────────────────────────────────────────────────────

    /**
     * Validate and package the project's current source code.
     *
     * Updates the project's [BuildStatus] in persistent storage and
     * returns the [BuildResult].
     */
    suspend fun buildProject(projectId: String): BuildResult

    /**
     * Return the launch path for a successfully built project.
     * Null if the project has not been built or the output file was deleted.
     */
    suspend fun getLaunchPath(projectId: String): String?
}
