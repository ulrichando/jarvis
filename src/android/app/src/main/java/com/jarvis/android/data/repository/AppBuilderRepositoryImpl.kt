package com.jarvis.android.data.repository

import android.content.Context
import android.util.Log
import com.jarvis.android.domain.model.AppProject
import com.jarvis.android.domain.model.AppTemplate
import com.jarvis.android.domain.model.AppType
import com.jarvis.android.domain.model.BuildResult
import com.jarvis.android.domain.model.BuildStatus
import com.jarvis.android.domain.model.GenerationRequest
import com.jarvis.android.domain.model.TemplateCategory
import com.jarvis.android.domain.repository.AppBuilderRepository
import com.jarvis.android.system.builder.AppBuildEngine
import com.jarvis.android.system.builder.AppCodeGenerator
import com.jarvis.android.system.builder.AppTemplateRegistry
import com.jarvis.android.system.builder.GenerationResult
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.update
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.File
import java.util.UUID
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Implements [AppBuilderRepository].
 *
 * ## Persistence
 *
 * Projects are serialised as JSON objects and stored individually in
 * `<filesDir>/builder/<id>.json`. This avoids a new Room migration while
 * keeping projects durable across restarts. The in-memory [MutableStateFlow]
 * is the reactive source of truth; the JSON files are the persisted backing.
 *
 * Project payloads contain only metadata + source code — built artefacts
 * live under `<filesDir>/apps/<id>/` and are managed by [AppBuildEngine].
 */
@Singleton
class AppBuilderRepositoryImpl @Inject constructor(
    @ApplicationContext private val context: Context,
    private val templateRegistry: AppTemplateRegistry,
    private val codeGenerator:    AppCodeGenerator,
    private val buildEngine:      AppBuildEngine,
) : AppBuilderRepository {

    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }

    /** In-memory list — the single source of truth for [observeProjects]. */
    private val _projects = MutableStateFlow<List<AppProject>>(emptyList())

    private val storageDir: File
        get() = File(context.filesDir, "builder").also { it.mkdirs() }

    init {
        _projects.value = loadAll()
    }

    // ── Templates ─────────────────────────────────────────────────────────────

    override fun getTemplates(): List<AppTemplate> = templateRegistry.getAll()

    override fun getTemplatesByCategory(category: TemplateCategory): List<AppTemplate> =
        templateRegistry.getByCategory(category)

    override fun getTemplateById(id: String): AppTemplate? = templateRegistry.getById(id)

    // ── Projects ──────────────────────────────────────────────────────────────

    override fun observeProjects(): Flow<List<AppProject>> = _projects

    override suspend fun getProject(id: String): AppProject? =
        _projects.value.find { it.id == id }

    override suspend fun createProject(
        name:       String,
        description: String,
        type:       AppType,
        templateId: String?,
    ): AppProject {
        val template = templateId?.let { templateRegistry.getById(it) }
        val project = AppProject(
            id          = "app_${UUID.randomUUID().toString().take(8)}",
            name        = name.trim(),
            description = description.trim(),
            type        = type,
            templateId  = templateId,
            sourceCode  = template?.sourceCode ?: "",
            buildStatus = if (template != null) BuildStatus.IDLE else BuildStatus.IDLE,
        )
        upsert(project)
        return project
    }

    override suspend fun updateSourceCode(projectId: String, code: String) {
        mutate(projectId) { it.copy(sourceCode = code, updatedAt = System.currentTimeMillis()) }
    }

    override suspend fun renameProject(projectId: String, name: String) {
        mutate(projectId) { it.copy(name = name.trim(), updatedAt = System.currentTimeMillis()) }
    }

    override suspend fun deleteProject(projectId: String) {
        buildEngine.clean(projectId)
        File(storageDir, "$projectId.json").delete()
        _projects.update { list -> list.filter { it.id != projectId } }
    }

    // ── AI generation ─────────────────────────────────────────────────────────

    override fun generateCode(request: GenerationRequest): Flow<String> = flow {
        emit("Routing inference…")
        mutateByRequest(request) { it.copy(buildStatus = BuildStatus.GENERATING) }

        emit("Generating code for \"${request.projectName}\"…")
        when (val result = codeGenerator.generate(request)) {
            is GenerationResult.Code -> {
                emit("CODE:${result.sourceCode}")
            }
            is GenerationResult.PromptForCloud -> {
                emit("CLOUD_PROMPT:${result.prompt}")
            }
            is GenerationResult.Error -> {
                emit("ERROR:${result.message}")
            }
        }
    }

    // ── Build ─────────────────────────────────────────────────────────────────

    override suspend fun buildProject(projectId: String): BuildResult {
        val project = getProject(projectId)
            ?: return BuildResult(projectId, success = false, errorMessage = "Project not found")

        mutate(projectId) { it.copy(buildStatus = BuildStatus.BUILDING) }

        val result = buildEngine.build(project)

        mutate(projectId) {
            it.copy(
                buildStatus  = if (result.success) BuildStatus.READY else BuildStatus.FAILED,
                outputPath   = result.outputPath ?: it.outputPath,
                errorMessage = result.errorMessage,
                updatedAt    = System.currentTimeMillis(),
            )
        }

        return result
    }

    override suspend fun getLaunchPath(projectId: String): String? {
        val project = getProject(projectId) ?: return null
        return buildEngine.launchPath(project)
    }

    // ── Private helpers ───────────────────────────────────────────────────────

    private fun upsert(project: AppProject) {
        _projects.update { list ->
            val existing = list.indexOfFirst { it.id == project.id }
            if (existing >= 0) list.toMutableList().also { it[existing] = project }
            else listOf(project) + list
        }
        persist(project)
    }

    private fun mutate(id: String, transform: (AppProject) -> AppProject) {
        _projects.update { list ->
            list.map { if (it.id == id) transform(it).also { p -> persist(p) } else it }
        }
    }

    private fun mutateByRequest(request: GenerationRequest, transform: (AppProject) -> AppProject) {
        // find project by name — best effort, called during generation flow
        _projects.update { list ->
            list.map { p ->
                if (p.name == request.projectName) transform(p).also { persist(it) } else p
            }
        }
    }

    private fun persist(project: AppProject) {
        try {
            File(storageDir, "${project.id}.json")
                .writeText(json.encodeToString(project.toStorable()), Charsets.UTF_8)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to persist project ${project.id}", e)
        }
    }

    private fun loadAll(): List<AppProject> {
        return storageDir.listFiles { f -> f.extension == "json" }
            ?.mapNotNull { file ->
                try {
                    json.decodeFromString<StorableProject>(file.readText()).toProject()
                } catch (e: Exception) {
                    Log.w(TAG, "Failed to load project from ${file.name}", e)
                    null
                }
            }
            ?.sortedByDescending { it.updatedAt }
            ?: emptyList()
    }

    companion object { private const val TAG = "AppBuilderRepo" }
}

// ── Serialisable project DTO ──────────────────────────────────────────────────
// AppProject is a domain model with enum fields — we use a flat DTO for JSON.

@kotlinx.serialization.Serializable
private data class StorableProject(
    val id:           String,
    val name:         String,
    val description:  String,
    val type:         String,
    val templateId:   String?,
    val sourceCode:   String,
    val buildStatus:  String,
    val outputPath:   String?,
    val errorMessage: String?,
    val createdAt:    Long,
    val updatedAt:    Long,
)

private fun AppProject.toStorable() = StorableProject(
    id          = id,
    name        = name,
    description = description,
    type        = type.name,
    templateId  = templateId,
    sourceCode  = sourceCode,
    buildStatus = buildStatus.name,
    outputPath  = outputPath,
    errorMessage = errorMessage,
    createdAt   = createdAt,
    updatedAt   = updatedAt,
)

private fun StorableProject.toProject() = AppProject(
    id          = id,
    name        = name,
    description = description,
    type        = AppType.valueOf(type),
    templateId  = templateId,
    sourceCode  = sourceCode,
    buildStatus = runCatching { BuildStatus.valueOf(buildStatus) }.getOrDefault(BuildStatus.IDLE),
    outputPath  = outputPath,
    errorMessage = errorMessage,
    createdAt   = createdAt,
    updatedAt   = updatedAt,
)
