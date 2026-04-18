package com.jarvis.android.presentation.builder

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Build
import androidx.compose.material.icons.filled.Code
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.DriveFileRenameOutline
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.ViewList
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.TabRowDefaults
import androidx.compose.material3.TabRowDefaults.tabIndicatorOffset
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import com.jarvis.android.core.designsystem.JarvisPalette
import com.jarvis.android.domain.model.AppProject
import com.jarvis.android.domain.model.AppTemplate
import com.jarvis.android.domain.model.AppType
import com.jarvis.android.domain.model.BuildStatus
import com.jarvis.android.domain.model.TemplateCategory

// ── Palette helpers ───────────────────────────────────────────────────────────

private val Surface  = Color(0xFF141414)
private val Gold     = JarvisPalette.GoldPrimary
private val Obsidian = JarvisPalette.ObsidianBlack
private val TextPrimary   = Color(0xFFF0EDE8)
private val TextSecondary = Color(0xFF888888)
private val Danger   = Color(0xFFCF6679)

// ── Root ──────────────────────────────────────────────────────────────────────

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AppBuilderScreen(
    onBack:      () -> Unit = {},
    onLaunchApp: (String) -> Unit = {},
    vm: AppBuilderViewModel = hiltViewModel(),
) {
    val ui by vm.ui.collectAsState()
    var selectedTab by rememberSaveable { mutableIntStateOf(0) }

    val tabs = listOf("Projects", "Builder", "Templates")

    Scaffold(
        containerColor = Obsidian,
        topBar = {
            Column {
                TopAppBar(
                    title = {
                        Text(
                            "App Builder",
                            color      = Gold,
                            fontWeight = FontWeight.Bold,
                            fontSize   = 18.sp,
                        )
                    },
                    navigationIcon = {
                        IconButton(onClick = onBack) {
                            Icon(Icons.Default.ArrowBack, contentDescription = "Back", tint = Gold)
                        }
                    },
                    actions = {
                        if (selectedTab == 0) {
                            IconButton(onClick = vm::showNewProjectDialog) {
                                Icon(Icons.Default.Add, contentDescription = "New project", tint = Gold)
                            }
                        }
                    },
                    colors = TopAppBarDefaults.topAppBarColors(containerColor = Obsidian),
                )
                TabRow(
                    selectedTabIndex = selectedTab,
                    containerColor   = Obsidian,
                    indicator = { tabPositions ->
                        TabRowDefaults.Indicator(
                            modifier = Modifier.tabIndicatorOffset(tabPositions[selectedTab]),
                            color    = Gold,
                        )
                    },
                ) {
                    tabs.forEachIndexed { i, label ->
                        Tab(
                            selected = selectedTab == i,
                            onClick  = { selectedTab = i },
                            text = {
                                Text(
                                    label,
                                    color = if (selectedTab == i) Gold else TextSecondary,
                                    fontSize = 13.sp,
                                )
                            },
                        )
                    }
                }
            }
        },
    ) { padding ->
        Box(Modifier.padding(padding).fillMaxSize()) {
            when (selectedTab) {
                0 -> ProjectsTab(ui, vm, onLaunchApp)
                1 -> BuilderTab(ui, vm, onLaunchApp)
                2 -> TemplatesTab(ui, vm) { selectedTab = 1 }
            }
        }
    }

    // ── Dialogs ───────────────────────────────────────────────────────────────

    if (ui.showNewProjectDialog) {
        NewProjectDialog(ui, vm)
    }
    if (ui.showRenameDialog) {
        RenameDialog(ui, vm)
    }
    if (ui.showDeleteConfirm) {
        DeleteConfirmDialog(ui, vm)
    }
    ui.errorMessage?.let { msg ->
        AlertDialog(
            onDismissRequest = vm::clearError,
            title = { Text("Error", color = Danger) },
            text  = { Text(msg, color = TextPrimary) },
            confirmButton = { TextButton(onClick = vm::clearError) { Text("OK", color = Gold) } },
            containerColor = Surface,
        )
    }
}

// ── Projects tab ──────────────────────────────────────────────────────────────

@Composable
private fun ProjectsTab(
    ui: BuilderUiState,
    vm: AppBuilderViewModel,
    onLaunchApp: (String) -> Unit,
) {
    if (ui.projects.isEmpty()) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Icon(Icons.Default.Code, null, tint = TextSecondary, modifier = Modifier.size(48.dp))
                Spacer(Modifier.height(12.dp))
                Text("No projects yet", color = TextSecondary, fontSize = 15.sp)
                Spacer(Modifier.height(8.dp))
                Button(
                    onClick = vm::showNewProjectDialog,
                    colors = ButtonDefaults.buttonColors(containerColor = Gold),
                ) { Text("Create Project", color = Obsidian) }
            }
        }
        return
    }

    LazyColumn(
        modifier = Modifier.fillMaxSize().padding(horizontal = 16.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        items(ui.projects, key = { it.id }) { project ->
            ProjectCard(project, vm, onLaunchApp)
        }
    }
}

@Composable
private fun ProjectCard(
    project: AppProject,
    vm: AppBuilderViewModel,
    onLaunchApp: (String) -> Unit,
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { vm.selectProject(project) },
        colors = CardDefaults.cardColors(containerColor = Surface),
        shape  = RoundedCornerShape(12.dp),
    ) {
        Column(Modifier.padding(14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    project.name,
                    color      = TextPrimary,
                    fontWeight = FontWeight.SemiBold,
                    fontSize   = 15.sp,
                    modifier   = Modifier.weight(1f),
                    maxLines   = 1,
                    overflow   = TextOverflow.Ellipsis,
                )
                BuildStatusBadge(project.buildStatus)
            }
            if (project.description.isNotBlank()) {
                Spacer(Modifier.height(4.dp))
                Text(project.description, color = TextSecondary, fontSize = 12.sp, maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
            Spacer(Modifier.height(10.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                AppTypeChip(project.type)
                Spacer(Modifier.weight(1f))
                // Rename
                IconButton(onClick = { vm.showRenameDialog(project) }, modifier = Modifier.size(32.dp)) {
                    Icon(Icons.Default.DriveFileRenameOutline, null, tint = TextSecondary, modifier = Modifier.size(18.dp))
                }
                // Delete
                IconButton(onClick = { vm.showDeleteConfirm(project) }, modifier = Modifier.size(32.dp)) {
                    Icon(Icons.Default.Delete, null, tint = Danger, modifier = Modifier.size(18.dp))
                }
                // Launch (only if READY)
                if (project.buildStatus == BuildStatus.READY) {
                    Button(
                        onClick = {
                            vm.selectProject(project)
                            project.outputPath?.let { onLaunchApp(it) }
                        },
                        colors  = ButtonDefaults.buttonColors(containerColor = Gold),
                        modifier = Modifier.height(32.dp),
                    ) {
                        Icon(Icons.Default.PlayArrow, null, tint = Obsidian, modifier = Modifier.size(16.dp))
                        Spacer(Modifier.width(4.dp))
                        Text("Run", color = Obsidian, fontSize = 12.sp)
                    }
                }
            }
        }
    }
}

// ── Builder tab ───────────────────────────────────────────────────────────────

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun BuilderTab(
    ui: BuilderUiState,
    vm: AppBuilderViewModel,
    onLaunchApp: (String) -> Unit,
) {
    if (ui.selectedProject == null) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Icon(Icons.Default.ViewList, null, tint = TextSecondary, modifier = Modifier.size(48.dp))
                Spacer(Modifier.height(12.dp))
                Text("Select a project from Projects tab", color = TextSecondary, fontSize = 14.sp)
            }
        }
        return
    }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        // ── Project name + type ───────────────────────────────────────────────
        Text(ui.selectedProject.name, color = Gold, fontWeight = FontWeight.Bold, fontSize = 16.sp)
        Text("Type", color = TextSecondary, fontSize = 11.sp)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            AppType.entries.forEach { type ->
                FilterChip(
                    selected = ui.selectedType == type,
                    onClick  = { vm.setAppType(type) },
                    label    = { Text(type.label, fontSize = 12.sp) },
                    colors   = FilterChipDefaults.filterChipColors(
                        selectedContainerColor     = Gold,
                        selectedLabelColor         = Obsidian,
                        containerColor             = Surface,
                        labelColor                 = TextSecondary,
                    ),
                )
            }
        }

        // ── Description ───────────────────────────────────────────────────────
        OutlinedTextField(
            value         = ui.description,
            onValueChange = vm::setDescription,
            label         = { Text("Describe your app", color = TextSecondary) },
            placeholder   = { Text("e.g. A calculator with history and dark theme", color = TextSecondary, fontSize = 13.sp) },
            modifier      = Modifier.fillMaxWidth(),
            minLines      = 3,
            maxLines      = 6,
            colors        = jarvisTextFieldColors(),
        )

        OutlinedTextField(
            value         = ui.extraHints,
            onValueChange = vm::setExtraHints,
            label         = { Text("Extra hints (optional)", color = TextSecondary) },
            modifier      = Modifier.fillMaxWidth(),
            maxLines      = 3,
            colors        = jarvisTextFieldColors(),
        )

        // ── Generate / cancel ─────────────────────────────────────────────────
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(
                onClick  = if (ui.isGenerating) vm::cancelGeneration else vm::generateCode,
                enabled  = if (ui.isGenerating) true else ui.canGenerate,
                colors   = ButtonDefaults.buttonColors(
                    containerColor         = if (ui.isGenerating) Danger else Gold,
                    disabledContainerColor = Color(0xFF333333),
                ),
                modifier = Modifier.weight(1f),
            ) {
                if (ui.isGenerating) {
                    CircularProgressIndicator(color = Color.White, strokeWidth = 2.dp, modifier = Modifier.size(16.dp))
                    Spacer(Modifier.width(8.dp))
                    Icon(Icons.Default.Stop, null, tint = Color.White)
                    Spacer(Modifier.width(4.dp))
                    Text("Cancel", color = Color.White)
                } else {
                    Icon(Icons.Default.Code, null, tint = Obsidian, modifier = Modifier.size(16.dp))
                    Spacer(Modifier.width(6.dp))
                    Text("Generate Code", color = Obsidian)
                }
            }
            Button(
                onClick  = vm::buildCurrentProject,
                enabled  = ui.canBuild,
                colors   = ButtonDefaults.buttonColors(
                    containerColor         = Color(0xFF1E3A2F),
                    disabledContainerColor = Color(0xFF333333),
                ),
            ) {
                Icon(Icons.Default.Build, null, tint = Color(0xFF4CAF50), modifier = Modifier.size(16.dp))
                Spacer(Modifier.width(4.dp))
                Text("Build", color = Color(0xFF4CAF50))
            }
        }

        // ── Generation log ────────────────────────────────────────────────────
        if (ui.generationLog.isNotEmpty()) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(Color(0xFF0D0D0D), RoundedCornerShape(8.dp))
                    .padding(10.dp),
            ) {
                ui.generationLog.forEach { line ->
                    Text(line, color = TextSecondary, fontSize = 11.sp, fontFamily = FontFamily.Monospace)
                }
            }
        }

        // ── Build result ──────────────────────────────────────────────────────
        ui.lastBuildResult?.let { result ->
            val bgColor = if (result.success) Color(0xFF1A2F1A) else Color(0xFF2F1A1A)
            val fgColor = if (result.success) Color(0xFF4CAF50) else Danger
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(bgColor, RoundedCornerShape(8.dp))
                    .padding(10.dp),
            ) {
                Text(
                    if (result.success) "Build successful — ${result.sizeBytes} bytes" else "Build failed",
                    color = fgColor, fontWeight = FontWeight.SemiBold, fontSize = 13.sp,
                )
                result.errorMessage?.let { err ->
                    Spacer(Modifier.height(4.dp))
                    Text(err, color = Danger, fontSize = 12.sp)
                }
                if (result.success) {
                    Spacer(Modifier.height(8.dp))
                    Button(
                        onClick = { ui.launchPath?.let { onLaunchApp(it) } },
                        colors  = ButtonDefaults.buttonColors(containerColor = Gold),
                    ) {
                        Icon(Icons.Default.PlayArrow, null, tint = Obsidian, modifier = Modifier.size(16.dp))
                        Spacer(Modifier.width(4.dp))
                        Text("Run App", color = Obsidian)
                    }
                }
            }
        }

        // ── Source code editor ────────────────────────────────────────────────
        Text("Source Code", color = TextSecondary, fontSize = 12.sp)
        OutlinedTextField(
            value         = ui.sourceCode,
            onValueChange = vm::setSourceCode,
            modifier      = Modifier
                .fillMaxWidth()
                .height(320.dp),
            textStyle = androidx.compose.ui.text.TextStyle(
                color      = Color(0xFF90EE90),
                fontFamily = FontFamily.Monospace,
                fontSize   = 11.sp,
                lineHeight = 16.sp,
            ),
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor   = Gold.copy(alpha = 0.5f),
                unfocusedBorderColor = Color(0xFF333333),
                cursorColor          = Gold,
                focusedContainerColor   = Color(0xFF0D0D0D),
                unfocusedContainerColor = Color(0xFF0D0D0D),
            ),
        )
    }
}

// ── Templates tab ─────────────────────────────────────────────────────────────

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun TemplatesTab(
    ui: BuilderUiState,
    vm: AppBuilderViewModel,
    onApplied: () -> Unit,
) {
    Column(Modifier.fillMaxSize()) {
        // Category filter row
        FlowRow(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            FilterChip(
                selected = ui.templateFilter == null,
                onClick  = { vm.setTemplateFilter(null) },
                label    = { Text("All", fontSize = 12.sp) },
                colors   = jarvisFilterChipColors(),
            )
            TemplateCategory.entries.forEach { cat ->
                FilterChip(
                    selected = ui.templateFilter == cat,
                    onClick  = { vm.setTemplateFilter(cat) },
                    label    = { Text(cat.name.lowercase().replaceFirstChar { it.uppercase() }, fontSize = 12.sp) },
                    colors   = jarvisFilterChipColors(),
                )
            }
        }

        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            items(ui.filteredTemplates, key = { it.id }) { template ->
                TemplateCard(template) {
                    vm.applyTemplate(template)
                    onApplied()
                }
            }
            item { Spacer(Modifier.height(16.dp)) }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun TemplateCard(template: AppTemplate, onApply: () -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors   = CardDefaults.cardColors(containerColor = Surface),
        shape    = RoundedCornerShape(12.dp),
    ) {
        Column(Modifier.padding(14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    template.name,
                    color      = TextPrimary,
                    fontWeight = FontWeight.SemiBold,
                    fontSize   = 14.sp,
                    modifier   = Modifier.weight(1f),
                )
                AppTypeChip(template.type)
            }
            Spacer(Modifier.height(4.dp))
            Text(template.description, color = TextSecondary, fontSize = 12.sp, maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (template.tags.isNotEmpty()) {
                Spacer(Modifier.height(8.dp))
                FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    template.tags.take(5).forEach { tag ->
                        Box(
                            Modifier
                                .background(Color(0xFF1C1C1C), RoundedCornerShape(4.dp))
                                .padding(horizontal = 6.dp, vertical = 2.dp),
                        ) {
                            Text(tag, color = TextSecondary, fontSize = 10.sp)
                        }
                    }
                }
            }
            Spacer(Modifier.height(10.dp))
            Button(
                onClick  = onApply,
                colors   = ButtonDefaults.buttonColors(containerColor = Color(0xFF1A1A1A)),
                modifier = Modifier.align(Alignment.End),
            ) {
                Text("Use Template", color = Gold, fontSize = 12.sp)
            }
        }
    }
}

// ── Dialogs ───────────────────────────────────────────────────────────────────

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun NewProjectDialog(ui: BuilderUiState, vm: AppBuilderViewModel) {
    AlertDialog(
        onDismissRequest = vm::hideNewProjectDialog,
        containerColor   = Surface,
        title            = { Text("New Project", color = Gold, fontWeight = FontWeight.Bold) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedTextField(
                    value         = ui.projectName,
                    onValueChange = vm::setProjectName,
                    label         = { Text("Project name", color = TextSecondary) },
                    singleLine    = true,
                    colors        = jarvisTextFieldColors(),
                    modifier      = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value         = ui.description,
                    onValueChange = vm::setDescription,
                    label         = { Text("Description", color = TextSecondary) },
                    maxLines      = 3,
                    colors        = jarvisTextFieldColors(),
                    modifier      = Modifier.fillMaxWidth(),
                )
                Text("App type", color = TextSecondary, fontSize = 11.sp)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    AppType.entries.forEach { type ->
                        FilterChip(
                            selected = ui.selectedType == type,
                            onClick  = { vm.setAppType(type) },
                            label    = { Text(type.label, fontSize = 12.sp) },
                            colors   = jarvisFilterChipColors(),
                        )
                    }
                }
            }
        },
        confirmButton = {
            Button(
                onClick  = vm::createNewProject,
                enabled  = ui.projectName.isNotBlank(),
                colors   = ButtonDefaults.buttonColors(containerColor = Gold),
            ) { Text("Create", color = Obsidian) }
        },
        dismissButton = {
            TextButton(onClick = vm::hideNewProjectDialog) { Text("Cancel", color = TextSecondary) }
        },
    )
}

@Composable
private fun RenameDialog(ui: BuilderUiState, vm: AppBuilderViewModel) {
    AlertDialog(
        onDismissRequest = vm::hideRenameDialog,
        containerColor   = Surface,
        title            = { Text("Rename Project", color = Gold) },
        text = {
            OutlinedTextField(
                value         = ui.projectName,
                onValueChange = vm::setProjectName,
                label         = { Text("New name", color = TextSecondary) },
                singleLine    = true,
                colors        = jarvisTextFieldColors(),
                modifier      = Modifier.fillMaxWidth(),
            )
        },
        confirmButton = {
            Button(
                onClick  = vm::renameCurrentProject,
                enabled  = ui.projectName.isNotBlank(),
                colors   = ButtonDefaults.buttonColors(containerColor = Gold),
            ) { Text("Rename", color = Obsidian) }
        },
        dismissButton = {
            TextButton(onClick = vm::hideRenameDialog) { Text("Cancel", color = TextSecondary) }
        },
    )
}

@Composable
private fun DeleteConfirmDialog(ui: BuilderUiState, vm: AppBuilderViewModel) {
    val project = ui.projectToActOn ?: return
    AlertDialog(
        onDismissRequest = vm::hideDeleteConfirm,
        containerColor   = Surface,
        title            = { Text("Delete Project", color = Danger) },
        text             = {
            Text(
                "Delete \"${project.name}\" and all its build artefacts? This cannot be undone.",
                color = TextPrimary,
            )
        },
        confirmButton = {
            Button(
                onClick = { vm.deleteProject(project) },
                colors  = ButtonDefaults.buttonColors(containerColor = Danger),
            ) { Text("Delete", color = Color.White) }
        },
        dismissButton = {
            TextButton(onClick = vm::hideDeleteConfirm) { Text("Cancel", color = TextSecondary) }
        },
    )
}

// ── Small components ──────────────────────────────────────────────────────────

@Composable
private fun BuildStatusBadge(status: BuildStatus) {
    val (label, color) = when (status) {
        BuildStatus.IDLE       -> "IDLE"       to TextSecondary
        BuildStatus.GENERATING -> "GENERATING" to Color(0xFF5B8EDE)
        BuildStatus.BUILDING   -> "BUILDING"   to Color(0xFFE0A030)
        BuildStatus.READY      -> "READY"      to Color(0xFF4CAF50)
        BuildStatus.FAILED     -> "FAILED"     to Danger
    }
    Text(
        label,
        color      = color,
        fontSize   = 10.sp,
        fontWeight = FontWeight.Bold,
        modifier   = Modifier
            .background(color.copy(alpha = 0.15f), RoundedCornerShape(4.dp))
            .padding(horizontal = 6.dp, vertical = 2.dp),
    )
}

@Composable
private fun AppTypeChip(type: AppType) {
    val color = when (type) {
        AppType.WEBVIEW -> Gold
        AppType.SHELL   -> Color(0xFF4CAF50)
        AppType.PYTHON  -> Color(0xFF5B8EDE)
    }
    Text(
        type.label,
        color    = color,
        fontSize = 10.sp,
        modifier = Modifier
            .background(color.copy(alpha = 0.15f), RoundedCornerShape(4.dp))
            .padding(horizontal = 6.dp, vertical = 2.dp),
    )
}

// ── Style helpers ──────────────────────────────────────────────────────────────

@Composable
private fun jarvisTextFieldColors() = OutlinedTextFieldDefaults.colors(
    focusedTextColor      = TextPrimary,
    unfocusedTextColor    = TextPrimary,
    focusedBorderColor    = Gold.copy(alpha = 0.6f),
    unfocusedBorderColor  = Color(0xFF333333),
    cursorColor           = Gold,
    focusedContainerColor    = Surface,
    unfocusedContainerColor  = Surface,
    focusedLabelColor     = Gold,
)

@Composable
private fun jarvisFilterChipColors() = FilterChipDefaults.filterChipColors(
    selectedContainerColor = Gold,
    selectedLabelColor     = Obsidian,
    containerColor         = Surface,
    labelColor             = TextSecondary,
)
