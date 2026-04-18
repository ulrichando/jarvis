package com.jarvis.android.presentation.builder

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.android.domain.model.AppProject
import com.jarvis.android.domain.model.AppTemplate
import com.jarvis.android.domain.model.AppType
import com.jarvis.android.domain.model.BuildResult
import com.jarvis.android.domain.model.BuildStatus
import com.jarvis.android.domain.model.GenerationRequest
import com.jarvis.android.domain.model.TemplateCategory
import com.jarvis.android.domain.usecase.builder.BuildProjectUseCase
import com.jarvis.android.domain.usecase.builder.CreateProjectUseCase
import com.jarvis.android.domain.usecase.builder.DeleteProjectUseCase
import com.jarvis.android.domain.usecase.builder.GenerateAppCodeUseCase
import com.jarvis.android.domain.usecase.builder.GetLaunchPathUseCase
import com.jarvis.android.domain.usecase.builder.GetTemplatesUseCase
import com.jarvis.android.domain.usecase.builder.ObserveProjectsUseCase
import com.jarvis.android.domain.usecase.builder.RenameProjectUseCase
import com.jarvis.android.domain.usecase.builder.UpdateSourceCodeUseCase
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

// ── UI state ──────────────────────────────────────────────────────────────────

data class BuilderUiState(
    // ── Projects tab ──
    val projects: List<AppProject> = emptyList(),

    // ── Builder tab ──
    val selectedProject: AppProject? = null,
    val projectName:     String = "",
    val description:     String = "",
    val extraHints:      String = "",
    val selectedType:    AppType = AppType.WEBVIEW,
    val selectedTemplateId: String? = null,
    val sourceCode:      String = "",
    val generationLog:   List<String> = emptyList(),
    val lastBuildResult: BuildResult? = null,
    val launchPath:      String? = null,

    // ── Templates tab ──
    val templates:       List<AppTemplate> = emptyList(),
    val templateFilter:  TemplateCategory? = null,

    // ── Common ──
    val isGenerating:    Boolean = false,
    val isBuilding:      Boolean = false,
    val errorMessage:    String? = null,
    val showNewProjectDialog:  Boolean = false,
    val showRenameDialog:      Boolean = false,
    val showDeleteConfirm:     Boolean = false,
    val projectToActOn:        AppProject? = null,
) {
    val canGenerate: Boolean
        get() = !isGenerating && !isBuilding && description.isNotBlank()

    val canBuild: Boolean
        get() = !isGenerating && !isBuilding && sourceCode.length >= 50

    val filteredTemplates: List<AppTemplate>
        get() = if (templateFilter == null) templates
                else templates.filter { it.category == templateFilter }
}

// ── ViewModel ─────────────────────────────────────────────────────────────────

@HiltViewModel
class AppBuilderViewModel @Inject constructor(
    private val observeProjects:    ObserveProjectsUseCase,
    private val createProject:      CreateProjectUseCase,
    private val updateSourceCode:   UpdateSourceCodeUseCase,
    private val renameProject:      RenameProjectUseCase,
    private val deleteProject:      DeleteProjectUseCase,
    private val generateAppCode:    GenerateAppCodeUseCase,
    private val buildProject:       BuildProjectUseCase,
    private val getLaunchPath:      GetLaunchPathUseCase,
    private val getTemplates:       GetTemplatesUseCase,
) : ViewModel() {

    private val _ui = MutableStateFlow(BuilderUiState())
    val ui: StateFlow<BuilderUiState> = _ui.asStateFlow()

    private var generationJob: Job? = null

    init {
        viewModelScope.launch {
            observeProjects().collect { list ->
                _ui.update { it.copy(projects = list) }
            }
        }
        _ui.update { it.copy(templates = getTemplates()) }
    }

    // ── Field setters ─────────────────────────────────────────────────────────

    fun setProjectName(v: String)     { _ui.update { it.copy(projectName = v) } }
    fun setDescription(v: String)     { _ui.update { it.copy(description = v) } }
    fun setExtraHints(v: String)      { _ui.update { it.copy(extraHints = v) } }
    fun setAppType(v: AppType)        { _ui.update { it.copy(selectedType = v) } }
    fun setTemplateFilter(v: TemplateCategory?) { _ui.update { it.copy(templateFilter = v) } }
    fun setSourceCode(v: String)      { _ui.update { it.copy(sourceCode = v) } }

    fun showNewProjectDialog()  { _ui.update { it.copy(showNewProjectDialog = true) } }
    fun hideNewProjectDialog()  { _ui.update { it.copy(showNewProjectDialog = false) } }
    fun showRenameDialog(p: AppProject)  { _ui.update { it.copy(showRenameDialog = true,  projectToActOn = p, projectName = p.name) } }
    fun hideRenameDialog()       { _ui.update { it.copy(showRenameDialog = false, projectToActOn = null) } }
    fun showDeleteConfirm(p: AppProject) { _ui.update { it.copy(showDeleteConfirm = true, projectToActOn = p) } }
    fun hideDeleteConfirm()      { _ui.update { it.copy(showDeleteConfirm = false, projectToActOn = null) } }
    fun clearError()             { _ui.update { it.copy(errorMessage = null) } }

    // ── Projects ──────────────────────────────────────────────────────────────

    fun selectProject(project: AppProject) {
        viewModelScope.launch {
            val path = getLaunchPath(project.id)
            _ui.update {
                it.copy(
                    selectedProject   = project,
                    projectName       = project.name,
                    description       = project.description,
                    selectedType      = project.type,
                    selectedTemplateId = project.templateId,
                    sourceCode        = project.sourceCode,
                    generationLog     = emptyList(),
                    lastBuildResult   = null,
                    launchPath        = path,
                )
            }
        }
    }

    fun createNewProject() {
        val s = _ui.value
        if (s.projectName.isBlank()) return
        viewModelScope.launch {
            val project = createProject(
                name        = s.projectName,
                description = s.description,
                type        = s.selectedType,
                templateId  = s.selectedTemplateId,
            )
            _ui.update {
                it.copy(
                    showNewProjectDialog = false,
                    selectedProject      = project,
                    sourceCode           = project.sourceCode,
                    generationLog        = emptyList(),
                )
            }
        }
    }

    fun renameCurrentProject() {
        val s = _ui.value
        val project = s.projectToActOn ?: return
        if (s.projectName.isBlank()) return
        viewModelScope.launch {
            renameProject(project.id, s.projectName)
            _ui.update { it.copy(showRenameDialog = false, projectToActOn = null) }
        }
    }

    fun deleteProject(project: AppProject) {
        viewModelScope.launch {
            deleteProject(project.id)
            _ui.update {
                val cleared = if (it.selectedProject?.id == project.id) null else it.selectedProject
                it.copy(
                    showDeleteConfirm = false,
                    projectToActOn    = null,
                    selectedProject   = cleared,
                    sourceCode        = if (cleared == null) "" else it.sourceCode,
                )
            }
        }
    }

    // ── Generation ────────────────────────────────────────────────────────────

    fun generateCode() {
        val s = _ui.value
        val project = s.selectedProject ?: return
        generationJob?.cancel()
        _ui.update { it.copy(isGenerating = true, generationLog = emptyList(), errorMessage = null) }

        generationJob = viewModelScope.launch {
            val request = GenerationRequest(
                projectName  = project.name,
                description  = s.description,
                type         = s.selectedType,
                templateBase = s.templates.find { it.id == s.selectedTemplateId }?.sourceCode,
                extraHints   = s.extraHints,
            )

            generateAppCode(request).collect { emission ->
                when {
                    emission.startsWith("CODE:") -> {
                        val code = emission.removePrefix("CODE:")
                        updateSourceCode(project.id, code)
                        _ui.update { it.copy(sourceCode = code, isGenerating = false) }
                    }
                    emission.startsWith("CLOUD_PROMPT:") -> {
                        val prompt = emission.removePrefix("CLOUD_PROMPT:")
                        _ui.update {
                            it.copy(
                                isGenerating = false,
                                generationLog = it.generationLog + "Cloud prompt ready — paste into Chat tab.",
                                sourceCode   = prompt,   // show prompt in editor so user can copy
                            )
                        }
                    }
                    emission.startsWith("ERROR:") -> {
                        _ui.update {
                            it.copy(
                                isGenerating = false,
                                errorMessage = emission.removePrefix("ERROR:"),
                            )
                        }
                    }
                    else -> {
                        _ui.update { it.copy(generationLog = it.generationLog + emission) }
                    }
                }
            }
        }
    }

    fun cancelGeneration() {
        generationJob?.cancel()
        _ui.update { it.copy(isGenerating = false) }
    }

    // ── Build ─────────────────────────────────────────────────────────────────

    fun buildCurrentProject() {
        val project = _ui.value.selectedProject ?: return
        viewModelScope.launch {
            _ui.update { it.copy(isBuilding = true, errorMessage = null) }
            val result = buildProject(project.id)
            val path   = if (result.success) getLaunchPath(project.id) else null
            _ui.update {
                it.copy(
                    isBuilding      = false,
                    lastBuildResult = result,
                    launchPath      = path,
                    errorMessage    = if (!result.success) result.errorMessage else null,
                )
            }
        }
    }

    // ── Templates ─────────────────────────────────────────────────────────────

    fun applyTemplate(template: AppTemplate) {
        _ui.update {
            it.copy(
                selectedTemplateId = template.id,
                selectedType       = template.type,
                sourceCode         = template.sourceCode,
                description        = if (it.description.isBlank()) template.description else it.description,
            )
        }
        val project = _ui.value.selectedProject ?: return
        viewModelScope.launch { updateSourceCode(project.id, template.sourceCode) }
    }
}
