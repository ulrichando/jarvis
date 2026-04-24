# App Builder UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the three-tab `AppBuilderScreen` with a stack-navigated Home / Project / Templates flow that uses the existing JARVIS theme tokens, gives each part of the `describe → generate → build → run` cycle its own focused surface, and drops the hardcoded colors that currently bypass the design system.

**Architecture:** One Hilt-injected `AppBuilderViewModel` scoped to a nested nav graph (`"app_builder"`) shared by Home, Project, Templates, and a full-screen CodeEditor. State reshaped with a new `selectedSegment` field and a derived `buildPhase`. A `Channel`-backed `navEvents` flow carries one-shot navigation signals from the VM to whichever screen is collecting. Every new composable reads from `MaterialTheme.colorScheme`, `MaterialTheme.typography`, and `LocalJarvisColors.current` — no hardcoded hex.

**Tech Stack:** Kotlin, Jetpack Compose (Material3), Navigation-Compose with `navigation { ... }` nested graphs, Hilt DI, Kotlin Coroutines + Flow, Turbine 1.2 for flow tests, JUnit 5 (Jupiter) + MockK for unit tests.

**Reference spec:** `docs/superpowers/specs/2026-04-23-app-builder-ui-redesign-design.md`

---

## File structure

### New files (all under `src/android/app/src/main/java/com/jarvis/android/presentation/builder/`)

```
builder/
├── AppBuilderHomeScreen.kt
├── AppBuilderProjectScreen.kt
├── AppBuilderTemplatesScreen.kt
├── CodeEditorScreen.kt
├── sheet/
│   └── NewProjectSheet.kt
├── pane/
│   ├── DescribePane.kt
│   ├── CodePane.kt
│   └── RunPane.kt
└── components/
    ├── ProjectCard.kt
    ├── StatusPill.kt
    ├── AppTypePill.kt
    ├── BuilderSegmentedControl.kt
    ├── TemplateCard.kt
    ├── GenerationLogStream.kt
    ├── BuildResultCard.kt
    ├── EmptyState.kt
    ├── CodeViewer.kt
    ├── SectionTitle.kt
    ├── JarvisTextFieldDefaults.kt
    └── Spacing.kt
```

### Modified files

- `builder/AppBuilderViewModel.kt` — add `ProjectSegment`, `BuildPhase`, `buildPhase`, `canRun`, `selectedSegment` field; add `selectSegment`, `selectProjectById`, `startProjectFromTemplate`; rename sheet flag and fns; add `navEvents` channel.
- `navigation/Screen.kt` — add `AppBuilder.Home`, `AppBuilder.Project`, `AppBuilder.Templates`, `AppBuilder.CodeEditor` routes as nested objects.
- `navigation/JarvisNavGraph.kt` — replace single `composable(Screen.AppBuilder.route)` with a `navigation(...)` sub-graph containing the four destinations.
- `presentation/settings/SettingsScreen.kt` — remove local `SectionTitle` function; import the shared one (only if trivially reachable, otherwise leave and note the duplication).

### Deleted files

- `builder/AppBuilderScreen.kt` (replaced by Home + Project + Templates + CodeEditor).

### Test files (under `src/android/app/src/test/java/com/jarvis/android/presentation/builder/`)

- `AppBuilderViewModelSegmentTest.kt`
- `AppBuilderViewModelBuildPhaseTest.kt`
- `AppBuilderViewModelNavEventsTest.kt`

---

## Conventions used throughout this plan

- **No `@file:OptIn`**: each composable that needs Material3 experimental APIs uses `@OptIn(ExperimentalMaterial3Api::class)` locally.
- **Imports in code blocks**: omitted for brevity. Let Android Studio / IDEA auto-import on paste.
- **Commits**: one per task. Conventional-commit style (`feat:`, `refactor:`, `test:`, `chore:`). No `Co-Authored-By` trailer (per repo convention).
- **Gradle path**: from the repo root, the Android app module is at `src/android/app`. Run all gradle commands from `/home/ulrich/Documents/Projects/jarvis/src/android/`. Example: `./gradlew :app:testDebugUnitTest`.
- **Composable tests**: there is no screenshot-test harness in this project. Each new composable ships with a `@Preview(uiMode = UI_MODE_NIGHT_YES)` and a `@Preview(uiMode = UI_MODE_NIGHT_NO)` for visual sanity inside the IDE. The `"test"` for a composable is: it compiles, and the preview renders.
- **TDD**: the VM tasks (§6–§10) are written test-first with Turbine. Composable tasks (§11–§25) are written implementation-first with `@Preview` as the validation. Navigation/integration tasks (§26–§30) are manual-smoke only.

---

## Phase 1 — Shared design-system atoms

These extractions are reused by every subsequent screen. Done first so later tasks don't block on them.

---

### Task 1: Spacing tokens

**Files:**
- Create: `src/android/app/src/main/java/com/jarvis/android/presentation/builder/components/Spacing.kt`

- [ ] **Step 1: Write the file**

```kotlin
package com.jarvis.android.presentation.builder.components

import androidx.compose.ui.unit.dp

/**
 * 4dp spacing grid for the App Builder screens.
 *
 * Private to the builder package by convention — if another feature wants these,
 * promote to core/designsystem. Kept local to avoid a project-wide refactor for
 * this redesign.
 */
object Space {
    val xs = 4.dp
    val sm = 8.dp
    val md = 12.dp
    val lg = 16.dp
    val xl = 24.dp
}
```

- [ ] **Step 2: Compile**

Run: `cd src/android && ./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Commit**

```bash
git add src/android/app/src/main/java/com/jarvis/android/presentation/builder/components/Spacing.kt
git commit -m "feat(app-builder): add Space tokens for 4dp spacing grid"
```

---

### Task 2: Shared text-field defaults

**Files:**
- Create: `src/android/app/src/main/java/com/jarvis/android/presentation/builder/components/JarvisTextFieldDefaults.kt`

- [ ] **Step 1: Write the file**

```kotlin
package com.jarvis.android.presentation.builder.components

import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.TextFieldColors
import androidx.compose.runtime.Composable
import androidx.compose.material3.MaterialTheme

/**
 * Outlined text-field colors used across App Builder screens. Replaces the
 * former per-file `jarvisTextFieldColors()` helper so every input looks the
 * same and every token lives in one place.
 */
@Composable
fun jarvisOutlinedTextFieldColors(): TextFieldColors {
    val cs = MaterialTheme.colorScheme
    return OutlinedTextFieldDefaults.colors(
        focusedTextColor        = cs.onSurface,
        unfocusedTextColor      = cs.onSurface,
        focusedBorderColor      = cs.primary.copy(alpha = 0.6f),
        unfocusedBorderColor    = cs.outline,
        cursorColor             = cs.primary,
        focusedContainerColor   = cs.surface,
        unfocusedContainerColor = cs.surface,
        focusedLabelColor       = cs.primary,
        unfocusedLabelColor     = cs.onSurfaceVariant,
    )
}
```

- [ ] **Step 2: Compile**

Run: `cd src/android && ./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Commit**

```bash
git add src/android/app/src/main/java/com/jarvis/android/presentation/builder/components/JarvisTextFieldDefaults.kt
git commit -m "feat(app-builder): extract shared jarvisOutlinedTextFieldColors"
```

---

### Task 3: Shared SectionTitle

**Files:**
- Create: `src/android/app/src/main/java/com/jarvis/android/presentation/builder/components/SectionTitle.kt`

- [ ] **Step 1: Write the file**

```kotlin
package com.jarvis.android.presentation.builder.components

import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

/**
 * Small uppercase-ish section title in primary color. Matches the pattern
 * used in SettingsScreen. Intended for the Home screen ("Projects"), the
 * Project screen ("Description", "Source code"), and the Templates screen
 * ("Start blank" / "Or pick a template").
 */
@Composable
fun SectionTitle(
    title: String,
    modifier: Modifier = Modifier,
) {
    Text(
        text     = title,
        style    = MaterialTheme.typography.labelMedium,
        color    = MaterialTheme.colorScheme.primary,
        modifier = modifier.padding(bottom = 6.dp),
    )
}
```

- [ ] **Step 2: Compile**

Run: `cd src/android && ./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Commit**

```bash
git add src/android/app/src/main/java/com/jarvis/android/presentation/builder/components/SectionTitle.kt
git commit -m "feat(app-builder): add shared SectionTitle composable"
```

*(Note: do NOT remove the local `SectionTitle` from SettingsScreen yet — that's a follow-up cleanup outside this plan's scope. The duplication is acceptable during migration.)*

---

### Task 4: StatusPill

**Files:**
- Create: `src/android/app/src/main/java/com/jarvis/android/presentation/builder/components/StatusPill.kt`

- [ ] **Step 1: Write the file**

```kotlin
package com.jarvis.android.presentation.builder.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.jarvis.android.core.designsystem.JarvisTheme
import com.jarvis.android.core.designsystem.LocalJarvisColors
import com.jarvis.android.presentation.builder.BuildPhase

/**
 * Capsule showing the current build phase. Spins for in-progress phases.
 *
 * READY      → success green
 * BUILDING   → warning amber + tiny spinner
 * GENERATING → primary (JARVIS blue) + tiny spinner
 * FAILED     → error
 * IDLE       → dim, no fill
 */
@Composable
fun StatusPill(
    phase: BuildPhase,
    modifier: Modifier = Modifier,
) {
    val jarvis = LocalJarvisColors.current
    val cs     = MaterialTheme.colorScheme

    val (label, fg, bg) = when (phase) {
        BuildPhase.READY      -> Triple("READY",      jarvis.successGreen, jarvis.successGreen.copy(alpha = 0.15f))
        BuildPhase.BUILDING   -> Triple("BUILDING",   jarvis.warningAmber, jarvis.warningAmber.copy(alpha = 0.15f))
        BuildPhase.GENERATING -> Triple("GENERATING", cs.primary,          cs.primary.copy(alpha = 0.15f))
        BuildPhase.FAILED     -> Triple("FAILED",     cs.error,            cs.error.copy(alpha = 0.15f))
        BuildPhase.IDLE       -> Triple("IDLE",       cs.onSurfaceVariant, Color.Transparent)
    }

    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = modifier
            .background(bg, RoundedCornerShape(999.dp))
            .padding(horizontal = 10.dp, vertical = 4.dp),
    ) {
        if (phase == BuildPhase.BUILDING || phase == BuildPhase.GENERATING) {
            CircularProgressIndicator(
                color       = fg,
                strokeWidth = 1.5.dp,
                modifier    = Modifier.size(10.dp),
            )
            Spacer(Modifier.width(6.dp))
        }
        Text(
            text  = label,
            style = MaterialTheme.typography.labelSmall,
            color = fg,
        )
    }
}

@Preview
@Composable
private fun StatusPillPreview() {
    JarvisTheme {
        androidx.compose.foundation.layout.Column {
            BuildPhase.values().forEach { StatusPill(it, Modifier.padding(4.dp)) }
        }
    }
}
```

*(This file references `BuildPhase`, which is added in Task 7. Temporarily add the enum inline here as `enum class BuildPhase { IDLE, GENERATING, BUILDING, READY, FAILED }` in `BuildPhase.kt` next to the VM, OR defer compile of this file until Task 7. The plan orders VM enums BEFORE StatusPill, so just sequence: do Task 7 first.)*

**Reordering note:** Do **Task 5 (AppTypePill)** → **Task 6 (EmptyState)** → **Task 7 (ProjectSegment + BuildPhase enums in VM)** → **then** Task 4 (StatusPill). Renumber mentally if executing strictly top-to-bottom.

- [ ] **Step 2: Compile** (after Task 7 lands)

Run: `cd src/android && ./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Commit**

```bash
git add src/android/app/src/main/java/com/jarvis/android/presentation/builder/components/StatusPill.kt
git commit -m "feat(app-builder): add StatusPill for BuildPhase display"
```

---

### Task 5: AppTypePill

**Files:**
- Create: `src/android/app/src/main/java/com/jarvis/android/presentation/builder/components/AppTypePill.kt`

- [ ] **Step 1: Write the file**

```kotlin
package com.jarvis.android.presentation.builder.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.jarvis.android.core.designsystem.JarvisTheme
import com.jarvis.android.core.designsystem.LocalJarvisColors
import com.jarvis.android.domain.model.AppType

/**
 * Tiny color-coded tag for an AppType.
 * Web  = primary blue
 * Shell = success green
 * Python = tertiary (user-bubble blue — warm lavender in dark, muted blue in light)
 */
@Composable
fun AppTypePill(
    type: AppType,
    modifier: Modifier = Modifier,
) {
    val cs     = MaterialTheme.colorScheme
    val jarvis = LocalJarvisColors.current

    val color: Color = when (type) {
        AppType.WEBVIEW -> cs.primary
        AppType.SHELL   -> jarvis.successGreen
        AppType.PYTHON  -> cs.tertiary
    }

    Text(
        text     = type.label,
        style    = MaterialTheme.typography.labelSmall,
        color    = color,
        modifier = modifier
            .background(color.copy(alpha = 0.15f), RoundedCornerShape(999.dp))
            .padding(horizontal = 10.dp, vertical = 3.dp),
    )
}

@Preview
@Composable
private fun AppTypePillPreview() {
    JarvisTheme {
        androidx.compose.foundation.layout.Row {
            AppType.entries.forEach { AppTypePill(it, Modifier.padding(4.dp)) }
        }
    }
}
```

- [ ] **Step 2: Compile**

Run: `cd src/android && ./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Commit**

```bash
git add src/android/app/src/main/java/com/jarvis/android/presentation/builder/components/AppTypePill.kt
git commit -m "feat(app-builder): add AppTypePill color-coded tag"
```

---

### Task 6: EmptyState

**Files:**
- Create: `src/android/app/src/main/java/com/jarvis/android/presentation/builder/components/EmptyState.kt`

- [ ] **Step 1: Write the file**

```kotlin
package com.jarvis.android.presentation.builder.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp

/**
 * Centered empty state with an icon, title, helper line, and optional primary CTA.
 * Used by Home ("No projects yet"), Code pane ("Generate first"), Run pane
 * ("Build first"), Templates ("No templates match filter").
 */
@Composable
fun EmptyState(
    icon:      ImageVector,
    title:     String,
    helper:    String? = null,
    ctaLabel:  String? = null,
    onCta:     (() -> Unit)? = null,
    modifier:  Modifier = Modifier,
) {
    Box(modifier.fillMaxSize().padding(Space.lg), contentAlignment = Alignment.Center) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Icon(
                imageVector  = icon,
                contentDescription = null,
                tint         = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier     = Modifier.size(48.dp),
            )
            Spacer(Modifier.height(Space.md))
            Text(
                text      = title,
                style     = MaterialTheme.typography.titleMedium,
                color     = MaterialTheme.colorScheme.onSurface,
                textAlign = TextAlign.Center,
            )
            if (helper != null) {
                Spacer(Modifier.height(Space.xs))
                Text(
                    text      = helper,
                    style     = MaterialTheme.typography.bodyMedium,
                    color     = MaterialTheme.colorScheme.onSurfaceVariant,
                    textAlign = TextAlign.Center,
                )
            }
            if (ctaLabel != null && onCta != null) {
                Spacer(Modifier.height(Space.lg))
                Button(
                    onClick = onCta,
                    colors  = ButtonDefaults.buttonColors(
                        containerColor = MaterialTheme.colorScheme.primary,
                        contentColor   = MaterialTheme.colorScheme.onPrimary,
                    ),
                ) { Text(ctaLabel) }
            }
        }
    }
}
```

- [ ] **Step 2: Compile**

Run: `cd src/android && ./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Commit**

```bash
git add src/android/app/src/main/java/com/jarvis/android/presentation/builder/components/EmptyState.kt
git commit -m "feat(app-builder): add EmptyState shared composable"
```

---

## Phase 2 — ViewModel changes (TDD with Turbine)

The ordering here matters: enums land first so the composables written in Phase 1 (StatusPill) and Phase 3 have the types they reference.

---

### Task 7: Add `ProjectSegment` and `BuildPhase` enums + `selectedSegment` field

**Files:**
- Modify: `src/android/app/src/main/java/com/jarvis/android/presentation/builder/AppBuilderViewModel.kt`
- Test: `src/android/app/src/test/java/com/jarvis/android/presentation/builder/AppBuilderViewModelSegmentTest.kt`

- [ ] **Step 1: Write the failing test**

Create `AppBuilderViewModelSegmentTest.kt`:

```kotlin
package com.jarvis.android.presentation.builder

import app.cash.turbine.test
import com.jarvis.android.domain.model.AppProject
import com.jarvis.android.domain.usecase.builder.BuildProjectUseCase
import com.jarvis.android.domain.usecase.builder.CreateProjectUseCase
import com.jarvis.android.domain.usecase.builder.DeleteProjectUseCase
import com.jarvis.android.domain.usecase.builder.GenerateAppCodeUseCase
import com.jarvis.android.domain.usecase.builder.GetLaunchPathUseCase
import com.jarvis.android.domain.usecase.builder.GetTemplatesUseCase
import com.jarvis.android.domain.usecase.builder.ObserveProjectsUseCase
import com.jarvis.android.domain.usecase.builder.RenameProjectUseCase
import com.jarvis.android.domain.usecase.builder.UpdateSourceCodeUseCase
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test

@OptIn(ExperimentalCoroutinesApi::class)
class AppBuilderViewModelSegmentTest {

    private val dispatcher = UnconfinedTestDispatcher()

    private lateinit var vm: AppBuilderViewModel

    @BeforeEach
    fun setUp() {
        Dispatchers.setMain(dispatcher)

        val observeProjects    = mockk<ObserveProjectsUseCase>()
        val createProject      = mockk<CreateProjectUseCase>()
        val updateSourceCode   = mockk<UpdateSourceCodeUseCase>()
        val renameProject      = mockk<RenameProjectUseCase>()
        val deleteProject      = mockk<DeleteProjectUseCase>()
        val generateAppCode    = mockk<GenerateAppCodeUseCase>()
        val buildProject       = mockk<BuildProjectUseCase>()
        val getLaunchPath      = mockk<GetLaunchPathUseCase>()
        val getTemplates       = mockk<GetTemplatesUseCase>()

        every { observeProjects()   } returns flowOf(emptyList())
        every { getTemplates()      } returns emptyList()

        vm = AppBuilderViewModel(
            observeProjects, createProject, updateSourceCode,
            renameProject, deleteProject, generateAppCode,
            buildProject, getLaunchPath, getTemplates,
        )
    }

    @AfterEach fun tearDown() { Dispatchers.resetMain() }

    @Test
    fun `default segment is DESCRIBE`() = runTest {
        vm.ui.test {
            assertEquals(ProjectSegment.DESCRIBE, awaitItem().selectedSegment)
        }
    }

    @Test
    fun `selectSegment emits the new segment`() = runTest {
        vm.ui.test {
            assertEquals(ProjectSegment.DESCRIBE, awaitItem().selectedSegment)
            vm.selectSegment(ProjectSegment.CODE)
            assertEquals(ProjectSegment.CODE, awaitItem().selectedSegment)
            vm.selectSegment(ProjectSegment.RUN)
            assertEquals(ProjectSegment.RUN, awaitItem().selectedSegment)
        }
    }

    @Test
    fun `selectSegment with the same value emits no new state`() = runTest {
        vm.selectSegment(ProjectSegment.CODE)
        vm.ui.test {
            assertEquals(ProjectSegment.CODE, awaitItem().selectedSegment)
            vm.selectSegment(ProjectSegment.CODE)
            expectNoEvents()
        }
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/android && ./gradlew :app:testDebugUnitTest --tests "*AppBuilderViewModelSegmentTest*"`
Expected: FAIL — `ProjectSegment` unresolved, `selectSegment` unresolved, `selectedSegment` unresolved.

- [ ] **Step 3: Add the enums and field to the VM**

At the top of `AppBuilderViewModel.kt`, after the imports and before `BuilderUiState`, add:

```kotlin
// ── Segment / phase enums ─────────────────────────────────────────────────────

enum class ProjectSegment { DESCRIBE, CODE, RUN }
enum class BuildPhase     { IDLE, GENERATING, BUILDING, READY, FAILED }
```

Inside the `BuilderUiState` data class, add the field (keep all existing fields):

```kotlin
data class BuilderUiState(
    // ...existing fields unchanged...
    val projectToActOn: AppProject? = null,

    // NEW
    val selectedSegment: ProjectSegment = ProjectSegment.DESCRIBE,
) {
    // ...existing derived props unchanged...
}
```

Inside the VM, add the setter near the other `set*` functions:

```kotlin
fun selectSegment(segment: ProjectSegment) {
    _ui.update { if (it.selectedSegment == segment) it else it.copy(selectedSegment = segment) }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/android && ./gradlew :app:testDebugUnitTest --tests "*AppBuilderViewModelSegmentTest*"`
Expected: PASS — 3 tests.

- [ ] **Step 5: Commit**

```bash
git add src/android/app/src/main/java/com/jarvis/android/presentation/builder/AppBuilderViewModel.kt \
        src/android/app/src/test/java/com/jarvis/android/presentation/builder/AppBuilderViewModelSegmentTest.kt
git commit -m "feat(app-builder): add ProjectSegment/BuildPhase + selectSegment (TDD)"
```

---

### Task 8: Derived `buildPhase` and `canRun` on `BuilderUiState`

**Files:**
- Modify: `src/android/app/src/main/java/com/jarvis/android/presentation/builder/AppBuilderViewModel.kt`
- Test: `src/android/app/src/test/java/com/jarvis/android/presentation/builder/AppBuilderViewModelBuildPhaseTest.kt`

- [ ] **Step 1: Write the failing test**

```kotlin
package com.jarvis.android.presentation.builder

import com.jarvis.android.domain.model.AppProject
import com.jarvis.android.domain.model.AppType
import com.jarvis.android.domain.model.BuildResult
import com.jarvis.android.domain.model.BuildStatus
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

class AppBuilderViewModelBuildPhaseTest {

    private val project = AppProject(
        id = "p", name = "p", description = "", type = AppType.WEBVIEW,
        templateId = null, sourceCode = "", buildStatus = BuildStatus.IDLE,
    )

    @Test
    fun `idle when nothing running and no build result`() {
        val s = BuilderUiState(selectedProject = project)
        assertEquals(BuildPhase.IDLE, s.buildPhase)
        assertFalse(s.canRun)
    }

    @Test
    fun `isGenerating takes precedence over everything`() {
        val ready = BuildResult(projectId = "p", success = true, outputPath = "/x", sizeBytes = 1)
        val s = BuilderUiState(
            selectedProject = project,
            isGenerating = true,
            lastBuildResult = ready,
        )
        assertEquals(BuildPhase.GENERATING, s.buildPhase)
    }

    @Test
    fun `isBuilding next in precedence`() {
        val s = BuilderUiState(selectedProject = project, isBuilding = true)
        assertEquals(BuildPhase.BUILDING, s.buildPhase)
    }

    @Test
    fun `successful lastBuildResult yields READY`() {
        val r = BuildResult(projectId = "p", success = true, outputPath = "/x", sizeBytes = 1)
        val s = BuilderUiState(selectedProject = project, lastBuildResult = r, launchPath = "/x")
        assertEquals(BuildPhase.READY, s.buildPhase)
        assertTrue(s.canRun)
    }

    @Test
    fun `failed lastBuildResult yields FAILED`() {
        val r = BuildResult(projectId = "p", success = false, errorMessage = "nope")
        val s = BuilderUiState(selectedProject = project, lastBuildResult = r)
        assertEquals(BuildPhase.FAILED, s.buildPhase)
        assertFalse(s.canRun)
    }

    @Test
    fun `project status READY without build result still shows READY`() {
        val readyProject = project.copy(buildStatus = BuildStatus.READY)
        val s = BuilderUiState(selectedProject = readyProject, launchPath = "/x")
        assertEquals(BuildPhase.READY, s.buildPhase)
        assertTrue(s.canRun)
    }

    @Test
    fun `canRun false without launchPath even when READY`() {
        val r = BuildResult(projectId = "p", success = true, outputPath = "/x", sizeBytes = 1)
        val s = BuilderUiState(selectedProject = project, lastBuildResult = r, launchPath = null)
        assertEquals(BuildPhase.READY, s.buildPhase)
        assertFalse(s.canRun)
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/android && ./gradlew :app:testDebugUnitTest --tests "*AppBuilderViewModelBuildPhaseTest*"`
Expected: FAIL — `buildPhase`, `canRun` unresolved.

- [ ] **Step 3: Add the derived properties to `BuilderUiState`**

Inside the `BuilderUiState` data class body, after the existing `canGenerate`, `canBuild`, `filteredTemplates` properties, add:

```kotlin
    val buildPhase: BuildPhase
        get() = when {
            isGenerating                                      -> BuildPhase.GENERATING
            isBuilding                                        -> BuildPhase.BUILDING
            lastBuildResult?.success == true                  -> BuildPhase.READY
            lastBuildResult?.success == false                 -> BuildPhase.FAILED
            selectedProject?.buildStatus == BuildStatus.READY -> BuildPhase.READY
            else                                              -> BuildPhase.IDLE
        }

    val canRun: Boolean
        get() = buildPhase == BuildPhase.READY && launchPath != null
```

- [ ] **Step 4: Run tests**

Run: `cd src/android && ./gradlew :app:testDebugUnitTest --tests "*AppBuilderViewModelBuildPhaseTest*"`
Expected: PASS — 7 tests.

- [ ] **Step 5: Commit**

```bash
git add src/android/app/src/main/java/com/jarvis/android/presentation/builder/AppBuilderViewModel.kt \
        src/android/app/src/test/java/com/jarvis/android/presentation/builder/AppBuilderViewModelBuildPhaseTest.kt
git commit -m "feat(app-builder): derive buildPhase and canRun on BuilderUiState (TDD)"
```

---

### Task 9: Rename `showNewProjectDialog` → `showNewProjectSheet`

**Files:**
- Modify: `src/android/app/src/main/java/com/jarvis/android/presentation/builder/AppBuilderViewModel.kt`

- [ ] **Step 1: Rename the field and methods**

In the `BuilderUiState` data class:

```kotlin
    // OLD: val showNewProjectDialog: Boolean = false,
    val showNewProjectSheet: Boolean = false,
```

In the VM body, replace the two methods:

```kotlin
    fun showNewProjectSheet() { _ui.update { it.copy(showNewProjectSheet = true) } }
    fun hideNewProjectSheet() { _ui.update { it.copy(showNewProjectSheet = false) } }
```

In the existing `createNewProject` function, change `showNewProjectDialog = false` to `showNewProjectSheet = false`.

- [ ] **Step 2: Verify no stale references in AppBuilderScreen.kt or anywhere**

Run: `grep -rn "showNewProjectDialog\|hideNewProjectDialog" src/android/app/src/main/`
Expected: only matches inside `AppBuilderScreen.kt` (will be deleted later). If any other file has them, update it in this same task.

`AppBuilderScreen.kt` references `vm.showNewProjectDialog()` / `hideNewProjectDialog()` and reads `ui.showNewProjectDialog`. Those will be removed when the file is deleted in Task 28 — for now, update them to the new names so the project still compiles:

In `AppBuilderScreen.kt`, replace:
- `vm::showNewProjectDialog` → `vm::showNewProjectSheet`
- `vm::hideNewProjectDialog` → `vm::hideNewProjectSheet`
- `ui.showNewProjectDialog` → `ui.showNewProjectSheet`

- [ ] **Step 3: Compile**

Run: `cd src/android && ./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 4: Commit**

```bash
git add src/android/app/src/main/java/com/jarvis/android/presentation/builder/
git commit -m "refactor(app-builder): rename showNewProjectDialog to showNewProjectSheet"
```

---

### Task 10: Add `navEvents` channel + emit from `createNewProject` and new `startProjectFromTemplate`

**Files:**
- Modify: `src/android/app/src/main/java/com/jarvis/android/presentation/builder/AppBuilderViewModel.kt`
- Test: `src/android/app/src/test/java/com/jarvis/android/presentation/builder/AppBuilderViewModelNavEventsTest.kt`

- [ ] **Step 1: Write the failing test**

```kotlin
package com.jarvis.android.presentation.builder

import app.cash.turbine.test
import com.jarvis.android.domain.model.AppProject
import com.jarvis.android.domain.model.AppTemplate
import com.jarvis.android.domain.model.AppType
import com.jarvis.android.domain.model.BuildStatus
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
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test

@OptIn(ExperimentalCoroutinesApi::class)
class AppBuilderViewModelNavEventsTest {

    private val dispatcher = UnconfinedTestDispatcher()

    private val mockProject = AppProject(
        id = "p-new", name = "My app", description = "",
        type = AppType.WEBVIEW, templateId = null, sourceCode = "",
        buildStatus = BuildStatus.IDLE,
    )

    private val template = AppTemplate(
        id = "t-1", name = "Weather", description = "Lock-screen weather",
        category = TemplateCategory.UTILITY, type = AppType.WEBVIEW,
        sourceCode = "<html/>", tags = listOf("weather"),
    )

    private lateinit var vm: AppBuilderViewModel
    private lateinit var createProject: CreateProjectUseCase
    private lateinit var updateSourceCode: UpdateSourceCodeUseCase

    @BeforeEach
    fun setUp() {
        Dispatchers.setMain(dispatcher)

        val observeProjects    = mockk<ObserveProjectsUseCase>()
        createProject          = mockk()
        updateSourceCode       = mockk(relaxed = true)
        val renameProject      = mockk<RenameProjectUseCase>()
        val deleteProject      = mockk<DeleteProjectUseCase>()
        val generateAppCode    = mockk<GenerateAppCodeUseCase>()
        val buildProject       = mockk<BuildProjectUseCase>()
        val getLaunchPath      = mockk<GetLaunchPathUseCase>(relaxed = true)
        val getTemplates       = mockk<GetTemplatesUseCase>()

        every  { observeProjects()   } returns flowOf(emptyList())
        every  { getTemplates()      } returns listOf(template)

        vm = AppBuilderViewModel(
            observeProjects, createProject, updateSourceCode,
            renameProject, deleteProject, generateAppCode,
            buildProject, getLaunchPath, getTemplates,
        )
    }

    @AfterEach fun tearDown() { Dispatchers.resetMain() }

    @Test
    fun `createNewProject emits exactly one OpenProject nav event`() = runTest {
        coEvery { createProject(any(), any(), any(), any()) } returns mockProject

        vm.setProjectName("My app")
        vm.setDescription("Does a thing")

        vm.navEvents.test {
            vm.createNewProject()
            val event = awaitItem()
            assertEquals(AppBuilderNav.OpenProject("p-new"), event)
            expectNoEvents()
        }
    }

    @Test
    fun `startProjectFromTemplate emits OpenProject and writes source code`() = runTest {
        coEvery { createProject(any(), any(), any(), any()) } returns mockProject

        vm.navEvents.test {
            vm.startProjectFromTemplate(template)
            assertEquals(AppBuilderNav.OpenProject("p-new"), awaitItem())
            expectNoEvents()
        }
    }

    @Test
    fun `createNewProject with blank name emits no nav event`() = runTest {
        vm.navEvents.test {
            vm.createNewProject()     // projectName is blank
            expectNoEvents()
        }
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/android && ./gradlew :app:testDebugUnitTest --tests "*AppBuilderViewModelNavEventsTest*"`
Expected: FAIL — `AppBuilderNav`, `navEvents`, `startProjectFromTemplate` unresolved.

- [ ] **Step 3: Add the nav-event types and channel to the VM**

In `AppBuilderViewModel.kt`, add new top-level declarations after the enums:

```kotlin
// ── Navigation events (one-shot) ──────────────────────────────────────────────

sealed interface AppBuilderNav {
    data class OpenProject(val projectId: String) : AppBuilderNav
}
```

Add new imports:

```kotlin
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.receiveAsFlow
```

Inside the VM class body, add the channel after the existing `_ui` StateFlow setup:

```kotlin
    private val _navEvents = Channel<AppBuilderNav>(capacity = Channel.BUFFERED)
    val navEvents: Flow<AppBuilderNav> = _navEvents.receiveAsFlow()
```

Modify `createNewProject` to emit on success (keep the existing state update, just add the send at the end of the viewModelScope.launch block, after the `_ui.update`):

```kotlin
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
                    showNewProjectSheet = false,
                    selectedProject     = project,
                    sourceCode          = project.sourceCode,
                    generationLog       = emptyList(),
                )
            }
            _navEvents.send(AppBuilderNav.OpenProject(project.id))
        }
    }
```

Add the new `startProjectFromTemplate` method (replacing the old `applyTemplate` behavior, since the old tab is gone):

```kotlin
    fun startProjectFromTemplate(template: AppTemplate) {
        viewModelScope.launch {
            val project = createProject(
                name        = template.name,
                description = template.description,
                type        = template.type,
                templateId  = template.id,
            )
            updateSourceCode(project.id, template.sourceCode)
            _ui.update {
                it.copy(
                    showNewProjectSheet = false,
                    selectedProject     = project,
                    projectName         = template.name,
                    description         = template.description,
                    selectedType        = template.type,
                    selectedTemplateId  = template.id,
                    sourceCode          = template.sourceCode,
                )
            }
            _navEvents.send(AppBuilderNav.OpenProject(project.id))
        }
    }
```

Keep the existing `applyTemplate` method **unchanged** for now — it's still referenced by `AppBuilderScreen.kt` (deleted later). Once that file is gone, a follow-up task removes it. Mark it for removal with a comment:

```kotlin
    // Deprecated: kept only for the legacy AppBuilderScreen.kt. Remove with that file.
    fun applyTemplate(template: AppTemplate) {
        ...existing body unchanged...
    }
```

- [ ] **Step 4: Run tests**

Run: `cd src/android && ./gradlew :app:testDebugUnitTest --tests "*AppBuilderViewModelNavEventsTest*"`
Expected: PASS — 3 tests.

- [ ] **Step 5: Also re-run the prior VM tests to confirm no regression**

Run: `cd src/android && ./gradlew :app:testDebugUnitTest --tests "*AppBuilderViewModel*"`
Expected: PASS — all VM tests.

- [ ] **Step 6: Commit**

```bash
git add src/android/app/src/main/java/com/jarvis/android/presentation/builder/AppBuilderViewModel.kt \
        src/android/app/src/test/java/com/jarvis/android/presentation/builder/AppBuilderViewModelNavEventsTest.kt
git commit -m "feat(app-builder): navEvents channel + startProjectFromTemplate (TDD)"
```

---

### Task 11: Add `selectProjectById(id)` for deep-link / nav hydration

**Files:**
- Modify: `src/android/app/src/main/java/com/jarvis/android/presentation/builder/AppBuilderViewModel.kt`

- [ ] **Step 1: Add the method**

Inside the VM, near the existing `selectProject(project: AppProject)`:

```kotlin
    /**
     * Hydrate state from a project ID. Used by [AppBuilderProjectScreen]
     * when the user navigates via the nav graph directly (no Home visit).
     * If the project isn't in [BuilderUiState.projects] yet, this waits
     * for the next projects emission before hydrating.
     */
    fun selectProjectById(id: String) {
        viewModelScope.launch {
            val current = _ui.value.projects.firstOrNull { it.id == id }
            if (current != null) {
                selectProject(current)
                return@launch
            }
            // Wait for the next emission that contains it.
            observeProjects().collect { list ->
                val found = list.firstOrNull { it.id == id }
                if (found != null) {
                    selectProject(found)
                    return@collect
                }
            }
        }
    }
```

*(Note: `selectProject(...)` is an existing method on the VM that already does the right thing.)*

- [ ] **Step 2: Compile**

Run: `cd src/android && ./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Commit**

```bash
git add src/android/app/src/main/java/com/jarvis/android/presentation/builder/AppBuilderViewModel.kt
git commit -m "feat(app-builder): add selectProjectById for nav hydration"
```

---

## Phase 3 — Composable atoms (Phase 1 continued, now with VM types available)

Now that Phase 2 has landed the enums, Tasks 4 (StatusPill) and any other composable referencing VM types can be completed. If executing strictly in numerical order, Task 4 should be run **after** Task 7 — the task text in Task 4 above notes this.

Skip ahead to Task 12 now; Task 4's checkbox is completed as part of that phase.

---

### Task 12: ProjectCard

**Files:**
- Create: `src/android/app/src/main/java/com/jarvis/android/presentation/builder/components/ProjectCard.kt`

- [ ] **Step 1: Write the file**

```kotlin
package com.jarvis.android.presentation.builder.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.jarvis.android.domain.model.AppProject
import com.jarvis.android.presentation.builder.BuildPhase

/**
 * Home-screen list row for an [AppProject].
 *
 * Tapping the card calls [onClick]. Tapping the overflow opens a dropdown
 * with Rename / Delete. The card renders a hairline [LinearProgressIndicator]
 * when [phase] is GENERATING or BUILDING.
 */
@Composable
fun ProjectCard(
    project:   AppProject,
    phase:     BuildPhase,
    onClick:   () -> Unit,
    onRename:  () -> Unit,
    onDelete:  () -> Unit,
    modifier:  Modifier = Modifier,
) {
    var menuOpen by remember { mutableStateOf(false) }

    Card(
        modifier = modifier.fillMaxWidth().clickable(onClick = onClick),
        colors   = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        shape    = RoundedCornerShape(12.dp),
    ) {
        Column(Modifier.padding(horizontal = Space.md, vertical = Space.md)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text       = project.name,
                    style      = MaterialTheme.typography.titleMedium,
                    color      = MaterialTheme.colorScheme.onSurface,
                    maxLines   = 1,
                    overflow   = TextOverflow.Ellipsis,
                    modifier   = Modifier.weight(1f),
                )
                StatusPill(phase)
                Box {
                    IconButton(onClick = { menuOpen = true }, modifier = Modifier.size(32.dp)) {
                        Icon(Icons.Outlined.MoreVert, contentDescription = "More", tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
                        DropdownMenuItem(text = { Text("Rename") }, onClick = { menuOpen = false; onRename() })
                        DropdownMenuItem(
                            text = { Text("Delete", color = MaterialTheme.colorScheme.error) },
                            onClick = { menuOpen = false; onDelete() },
                        )
                    }
                }
            }
            if (project.description.isNotBlank()) {
                Spacer(Modifier.height(Space.xs))
                Text(
                    text       = project.description,
                    style      = MaterialTheme.typography.bodySmall,
                    color      = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines   = 2,
                    overflow   = TextOverflow.Ellipsis,
                )
            }
            Spacer(Modifier.height(Space.sm))
            Row(verticalAlignment = Alignment.CenterVertically) {
                AppTypePill(project.type)
                Spacer(Modifier.weight(1f))
                Text(
                    text  = formatRelative(project.updatedAt),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (phase == BuildPhase.BUILDING || phase == BuildPhase.GENERATING) {
                Spacer(Modifier.height(Space.sm))
                LinearProgressIndicator(
                    modifier = Modifier.fillMaxWidth().height(2.dp),
                    color    = MaterialTheme.colorScheme.primary,
                    trackColor = MaterialTheme.colorScheme.surfaceVariant,
                )
            }
        }
    }
}

private fun formatRelative(timestampMs: Long): String {
    val diffMs = System.currentTimeMillis() - timestampMs
    val diffMin = diffMs / 60_000
    return when {
        diffMin < 1       -> "now"
        diffMin < 60      -> "${diffMin}m ago"
        diffMin < 60 * 24 -> "${diffMin / 60}h ago"
        else              -> "${diffMin / (60 * 24)}d ago"
    }
}
```

- [ ] **Step 2: Compile**

Run: `cd src/android && ./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Commit**

```bash
git add src/android/app/src/main/java/com/jarvis/android/presentation/builder/components/ProjectCard.kt
git commit -m "feat(app-builder): add ProjectCard with StatusPill + overflow menu"
```

---

### Task 13: BuilderSegmentedControl

**Files:**
- Create: `src/android/app/src/main/java/com/jarvis/android/presentation/builder/components/BuilderSegmentedControl.kt`

- [ ] **Step 1: Write the file**

```kotlin
package com.jarvis.android.presentation.builder.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.dp
import com.jarvis.android.presentation.builder.ProjectSegment

/**
 * Three-segment control for switching between the Describe, Code, and Run
 * panes inside [com.jarvis.android.presentation.builder.AppBuilderProjectScreen].
 *
 * Active segment gets a raised `surfaceVariant` pill and a primary-color label.
 */
@Composable
fun BuilderSegmentedControl(
    selected: ProjectSegment,
    onSelect: (ProjectSegment) -> Unit,
    modifier: Modifier = Modifier,
) {
    val cs = MaterialTheme.colorScheme

    Row(
        modifier = modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(10.dp))
            .background(cs.surface)
            .border(1.dp, cs.outlineVariant, RoundedCornerShape(10.dp))
            .padding(3.dp),
    ) {
        ProjectSegment.values().forEach { seg ->
            val active = seg == selected
            Box(
                modifier = Modifier
                    .weight(1f)
                    .clip(RoundedCornerShape(7.dp))
                    .background(if (active) cs.surfaceVariant else androidx.compose.ui.graphics.Color.Transparent)
                    .clickable { onSelect(seg) }
                    .padding(vertical = 8.dp),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    text  = seg.name.lowercase().replaceFirstChar { it.uppercase() },
                    style = MaterialTheme.typography.labelLarge,
                    color = if (active) cs.primary else cs.onSurfaceVariant,
                )
            }
        }
    }
}
```

- [ ] **Step 2: Compile**

Run: `cd src/android && ./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Commit**

```bash
git add src/android/app/src/main/java/com/jarvis/android/presentation/builder/components/BuilderSegmentedControl.kt
git commit -m "feat(app-builder): add BuilderSegmentedControl for Describe/Code/Run"
```

---

### Task 14: GenerationLogStream

**Files:**
- Create: `src/android/app/src/main/java/com/jarvis/android/presentation/builder/components/GenerationLogStream.kt`

- [ ] **Step 1: Write the file**

```kotlin
package com.jarvis.android.presentation.builder.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.jarvis.android.core.designsystem.LocalJarvisColors
import com.jarvis.android.core.designsystem.LocalJarvisTypography

/**
 * Auto-scrolling, terminal-styled log stream. The newest line is rendered
 * at full opacity; older lines fade.
 */
@Composable
fun GenerationLogStream(
    lines: List<String>,
    modifier: Modifier = Modifier,
) {
    val jarvis = LocalJarvisColors.current
    val typo   = LocalJarvisTypography.current
    val scroll = rememberLazyListState()

    LaunchedEffect(lines.size) {
        if (lines.isNotEmpty()) scroll.animateScrollToItem(lines.size - 1)
    }

    if (lines.isEmpty()) return

    LazyColumn(
        state    = scroll,
        modifier = modifier
            .fillMaxWidth()
            .background(jarvis.codeBg, RoundedCornerShape(8.dp))
            .padding(horizontal = Space.md, vertical = Space.sm),
    ) {
        itemsIndexed(lines) { idx, line ->
            val alpha = if (idx == lines.lastIndex) 1f else 0.6f
            Text(
                text     = line,
                style    = typo.terminalBody,
                color    = jarvis.terminalText.copy(alpha = alpha),
                modifier = Modifier.padding(vertical = 1.dp),
            )
        }
    }
}
```

- [ ] **Step 2: Compile**

Run: `cd src/android && ./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Commit**

```bash
git add src/android/app/src/main/java/com/jarvis/android/presentation/builder/components/GenerationLogStream.kt
git commit -m "feat(app-builder): add GenerationLogStream auto-scrolling log"
```

---

### Task 15: BuildResultCard

**Files:**
- Create: `src/android/app/src/main/java/com/jarvis/android/presentation/builder/components/BuildResultCard.kt`

- [ ] **Step 1: Write the file**

```kotlin
package com.jarvis.android.presentation.builder.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Error
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.jarvis.android.core.designsystem.LocalJarvisColors
import com.jarvis.android.domain.model.BuildResult

/**
 * Banner for the most recent [BuildResult].
 *
 * Success: green container with size + Run button (if [onRun] != null).
 * Failure: red container with error message + Retry button (if [onRetry] != null).
 */
@Composable
fun BuildResultCard(
    result:  BuildResult,
    onRun:   (() -> Unit)? = null,
    onRetry: (() -> Unit)? = null,
    modifier: Modifier = Modifier,
) {
    val cs     = MaterialTheme.colorScheme
    val jarvis = LocalJarvisColors.current

    val (bg, fg, icon, headline) = if (result.success) {
        val bg = jarvis.successGreen.copy(alpha = 0.12f)
        QuadResult(bg, jarvis.successGreen, Icons.Filled.CheckCircle, "Build successful · ${formatBytes(result.sizeBytes)}")
    } else {
        QuadResult(cs.errorContainer, cs.error, Icons.Filled.Error, "Build failed")
    }

    Column(
        modifier = modifier
            .fillMaxWidth()
            .background(bg, RoundedCornerShape(12.dp))
            .padding(Space.md),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(icon, contentDescription = null, tint = fg, modifier = Modifier.padding(end = Space.sm))
            Text(headline, style = MaterialTheme.typography.titleSmall, color = fg)
        }
        if (!result.success && !result.errorMessage.isNullOrBlank()) {
            Spacer(Modifier.height(Space.xs))
            Text(
                result.errorMessage,
                style = MaterialTheme.typography.bodySmall,
                color = fg.copy(alpha = 0.9f),
            )
        }
        if (result.success && onRun != null) {
            Spacer(Modifier.height(Space.md))
            Button(
                onClick = onRun,
                colors  = ButtonDefaults.buttonColors(
                    containerColor = cs.primary,
                    contentColor   = cs.onPrimary,
                ),
            ) {
                Icon(Icons.Filled.PlayArrow, contentDescription = null, modifier = Modifier.padding(end = 4.dp))
                Text("Run")
            }
        }
        if (!result.success && onRetry != null) {
            Spacer(Modifier.height(Space.md))
            Button(onClick = onRetry) { Text("Retry build") }
        }
    }
}

private data class QuadResult(val bg: Color, val fg: Color, val icon: androidx.compose.ui.graphics.vector.ImageVector, val headline: String)

private fun formatBytes(bytes: Long): String = when {
    bytes < 1024        -> "$bytes B"
    bytes < 1024 * 1024 -> "${bytes / 1024} KB"
    else                -> "${bytes / (1024 * 1024)} MB"
}
```

- [ ] **Step 2: Compile**

Run: `cd src/android && ./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Commit**

```bash
git add src/android/app/src/main/java/com/jarvis/android/presentation/builder/components/BuildResultCard.kt
git commit -m "feat(app-builder): add BuildResultCard success/failure banner"
```

---

### Task 16: CodeViewer (LazyColumn, read-only, basic token coloring)

**Files:**
- Create: `src/android/app/src/main/java/com/jarvis/android/presentation/builder/components/CodeViewer.kt`

- [ ] **Step 1: Write the file**

```kotlin
package com.jarvis.android.presentation.builder.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import com.jarvis.android.core.designsystem.LocalJarvisColors
import com.jarvis.android.core.designsystem.LocalJarvisTypography
import com.jarvis.android.domain.model.AppType

/**
 * Read-only source viewer with basic regex-based token coloring for
 * HTML / Bash / Python. Uses [LazyColumn] so 10k-line files scroll without jank.
 */
@Composable
fun CodeViewer(
    source:   String,
    type:     AppType,
    modifier: Modifier = Modifier,
) {
    val jarvis = LocalJarvisColors.current
    val typo   = LocalJarvisTypography.current

    // Split once and memoize; the view stays the same as long as source doesn't change.
    val lines = remember(source) { source.lines() }

    Box(
        modifier = modifier
            .fillMaxWidth()
            .background(jarvis.codeBg, RoundedCornerShape(8.dp))
            .border(1.dp, jarvis.codeBorder, RoundedCornerShape(8.dp)),
    ) {
        LazyColumn(Modifier.fillMaxWidth().padding(Space.sm)) {
            itemsIndexed(lines) { idx, line ->
                Row {
                    Text(
                        text  = (idx + 1).toString().padStart(4),
                        style = typo.terminalBody,
                        color = jarvis.textDisabled,
                        modifier = Modifier.width(36.dp),
                    )
                    Text(
                        text  = highlight(line, type, jarvis.terminalText, jarvis.goldGlow, jarvis.successGreen, jarvis.textDisabled),
                        style = typo.terminalBody,
                    )
                }
            }
        }
    }
}

private fun highlight(
    line:     String,
    type:     AppType,
    base:     Color,
    keyword:  Color,
    string:   Color,
    comment:  Color,
): AnnotatedString {
    val (kwRegex, strRegex, cmtRegex) = when (type) {
        AppType.WEBVIEW -> Triple(
            Regex("""</?[a-zA-Z][\w-]*"""),
            Regex("""\"[^\"]*\"|'[^']*'"""),
            Regex("""<!--.*?-->"""),
        )
        AppType.SHELL -> Triple(
            Regex("""\b(if|then|else|fi|for|do|done|while|in|case|esac|function|return|echo|exit|set|unset|source|export)\b"""),
            Regex("""\"[^\"]*\"|'[^']*'"""),
            Regex("""#.*$"""),
        )
        AppType.PYTHON -> Triple(
            Regex("""\b(def|class|return|if|elif|else|for|while|import|from|as|try|except|finally|raise|with|lambda|pass|in|not|and|or|is|None|True|False)\b"""),
            Regex("""\"[^\"]*\"|'[^']*'"""),
            Regex("""#.*$"""),
        )
    }

    return buildAnnotatedString {
        withStyle(SpanStyle(color = base)) { append(line) }
        cmtRegex.findAll(line).forEach { m -> addStyle(SpanStyle(color = comment), m.range.first, m.range.last + 1) }
        strRegex.findAll(line).forEach { m -> addStyle(SpanStyle(color = string),  m.range.first, m.range.last + 1) }
        kwRegex.findAll(line).forEach  { m -> addStyle(SpanStyle(color = keyword), m.range.first, m.range.last + 1) }
    }
}
```

- [ ] **Step 2: Compile**

Run: `cd src/android && ./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Commit**

```bash
git add src/android/app/src/main/java/com/jarvis/android/presentation/builder/components/CodeViewer.kt
git commit -m "feat(app-builder): add CodeViewer with regex token coloring"
```

---

### Task 17: TemplateCard (redesigned)

**Files:**
- Create: `src/android/app/src/main/java/com/jarvis/android/presentation/builder/components/TemplateCard.kt`

- [ ] **Step 1: Write the file**

```kotlin
package com.jarvis.android.presentation.builder.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.jarvis.android.domain.model.AppTemplate
import com.jarvis.android.domain.model.TemplateCategory

/**
 * Template gallery card. Whole-card tap invokes [onApply], which the host
 * screen wires to `vm.startProjectFromTemplate(template)`.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun TemplateCard(
    template: AppTemplate,
    onApply:  () -> Unit,
    modifier: Modifier = Modifier,
) {
    val cs = MaterialTheme.colorScheme

    Card(
        modifier = modifier.fillMaxWidth().clickable(onClick = onApply),
        colors   = CardDefaults.cardColors(containerColor = cs.surface),
        shape    = RoundedCornerShape(12.dp),
    ) {
        Column {
            // Gradient thumbnail placeholder based on category — no image assets
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(64.dp)
                    .background(categoryGradient(template.category, cs.primary)),
            )
            Column(Modifier.padding(Space.md)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        text     = template.name,
                        style    = MaterialTheme.typography.titleSmall,
                        color    = cs.onSurface,
                        modifier = Modifier.weight(1f),
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    AppTypePill(template.type)
                }
                Spacer(Modifier.height(Space.xs))
                Text(
                    text     = template.description,
                    style    = MaterialTheme.typography.bodySmall,
                    color    = cs.onSurfaceVariant,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                if (template.tags.isNotEmpty()) {
                    Spacer(Modifier.height(Space.sm))
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(Space.xs)) {
                        template.tags.take(5).forEach { tag ->
                            Text(
                                text  = tag,
                                style = MaterialTheme.typography.labelSmall,
                                color = cs.onSurfaceVariant,
                                modifier = Modifier
                                    .background(cs.surfaceVariant, RoundedCornerShape(6.dp))
                                    .padding(horizontal = Space.sm, vertical = 2.dp),
                            )
                        }
                    }
                }
            }
        }
    }
}

private fun categoryGradient(category: TemplateCategory, accent: Color): Brush {
    val stop = when (category) {
        TemplateCategory.UTILITY      -> Color(0xFF0E3A70)
        TemplateCategory.SYSTEM       -> Color(0xFF1A2F1A)
        TemplateCategory.PRODUCTIVITY -> Color(0xFF2A1A30)
        TemplateCategory.MEDIA        -> Color(0xFF2A2A1A)
        TemplateCategory.DEVELOPER    -> Color(0xFF1A2A30)
        TemplateCategory.GAME         -> Color(0xFF301A1A)
    }
    return Brush.linearGradient(listOf(stop, Color(0xFF141414)))
}
```

- [ ] **Step 2: Compile**

Run: `cd src/android && ./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Commit**

```bash
git add src/android/app/src/main/java/com/jarvis/android/presentation/builder/components/TemplateCard.kt
git commit -m "feat(app-builder): add redesigned TemplateCard with category gradient"
```

---

### Task 18: NewProjectSheet

**Files:**
- Create: `src/android/app/src/main/java/com/jarvis/android/presentation/builder/sheet/NewProjectSheet.kt`

- [ ] **Step 1: Write the file**

```kotlin
package com.jarvis.android.presentation.builder.sheet

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.unit.dp
import com.jarvis.android.domain.model.AppType
import com.jarvis.android.presentation.builder.components.SectionTitle
import com.jarvis.android.presentation.builder.components.Space
import com.jarvis.android.presentation.builder.components.jarvisOutlinedTextFieldColors

/**
 * "+" action on Home opens this sheet. Thin wrapper — stateful reads come from
 * the shared ViewModel via callbacks; the sheet itself owns no state beyond
 * the focus request.
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun NewProjectSheet(
    name:              String,
    description:       String,
    selectedType:      AppType,
    onNameChange:      (String) -> Unit,
    onDescriptionChange: (String) -> Unit,
    onTypeChange:      (AppType) -> Unit,
    onCreate:          () -> Unit,
    onStartFromTemplate: () -> Unit,
    onDismiss:         () -> Unit,
    canCreate:         Boolean,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val focus      = remember { FocusRequester() }

    LaunchedEffect(Unit) { focus.requestFocus() }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState       = sheetState,
        containerColor   = MaterialTheme.colorScheme.surface,
    ) {
        Column(Modifier.padding(horizontal = Space.lg, vertical = Space.md)) {
            SectionTitle("New project")
            Spacer(Modifier.height(Space.sm))
            OutlinedTextField(
                value         = name,
                onValueChange = onNameChange,
                label         = { Text("Name") },
                singleLine    = true,
                modifier      = Modifier.fillMaxWidth().focusRequester(focus),
                colors        = jarvisOutlinedTextFieldColors(),
            )
            Spacer(Modifier.height(Space.sm))
            OutlinedTextField(
                value         = description,
                onValueChange = onDescriptionChange,
                label         = { Text("One-line description") },
                maxLines      = 3,
                modifier      = Modifier.fillMaxWidth(),
                colors        = jarvisOutlinedTextFieldColors(),
            )
            Spacer(Modifier.height(Space.md))
            Text("Type", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(Space.xs))
            FlowRow(horizontalArrangement = Arrangement.spacedBy(Space.sm)) {
                AppType.entries.forEach { t ->
                    FilterChip(
                        selected = selectedType == t,
                        onClick  = { onTypeChange(t) },
                        label    = { Text(t.label) },
                        colors   = FilterChipDefaults.filterChipColors(
                            selectedContainerColor = MaterialTheme.colorScheme.primary,
                            selectedLabelColor     = MaterialTheme.colorScheme.onPrimary,
                        ),
                    )
                }
            }
            Spacer(Modifier.height(Space.lg))
            Button(
                onClick  = onCreate,
                enabled  = canCreate,
                modifier = Modifier.fillMaxWidth(),
                colors   = ButtonDefaults.buttonColors(
                    containerColor = MaterialTheme.colorScheme.primary,
                    contentColor   = MaterialTheme.colorScheme.onPrimary,
                ),
            ) { Text("Create") }
            Spacer(Modifier.height(Space.sm))
            HorizontalDivider()
            Spacer(Modifier.height(Space.sm))
            OutlinedButton(
                onClick  = onStartFromTemplate,
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Start from template →") }
            Spacer(Modifier.height(Space.md))
        }
    }
}
```

*Note: the `import androidx.compose.runtime.remember` may be needed — if the IDE doesn't auto-add it, do it manually.*

- [ ] **Step 2: Compile**

Run: `cd src/android && ./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Commit**

```bash
git add src/android/app/src/main/java/com/jarvis/android/presentation/builder/sheet/NewProjectSheet.kt
git commit -m "feat(app-builder): add NewProjectSheet ModalBottomSheet"
```

---

### Task 19: DescribePane

**Files:**
- Create: `src/android/app/src/main/java/com/jarvis/android/presentation/builder/pane/DescribePane.kt`

- [ ] **Step 1: Write the file**

```kotlin
package com.jarvis.android.presentation.builder.pane

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Code
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.jarvis.android.domain.model.AppType
import com.jarvis.android.presentation.builder.BuilderUiState
import com.jarvis.android.presentation.builder.components.GenerationLogStream
import com.jarvis.android.presentation.builder.components.SectionTitle
import com.jarvis.android.presentation.builder.components.Space
import com.jarvis.android.presentation.builder.components.jarvisOutlinedTextFieldColors

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun DescribePane(
    ui: BuilderUiState,
    onDescriptionChange: (String) -> Unit,
    onHintsChange:       (String) -> Unit,
    onTypeChange:        (AppType) -> Unit,
    onGenerate:          () -> Unit,
    onCancel:            () -> Unit,
    modifier:            Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .verticalScroll(rememberScrollState())
            .padding(Space.lg),
        verticalArrangement = Arrangement.spacedBy(Space.md),
    ) {
        SectionTitle("Describe your app")
        OutlinedTextField(
            value         = ui.description,
            onValueChange = onDescriptionChange,
            placeholder   = { Text("e.g. Lock-screen weather tile with forecast", color = MaterialTheme.colorScheme.onSurfaceVariant) },
            modifier      = Modifier.fillMaxWidth(),
            minLines      = 3,
            maxLines      = 6,
            colors        = jarvisOutlinedTextFieldColors(),
        )

        SectionTitle("Extra hints (optional)")
        OutlinedTextField(
            value         = ui.extraHints,
            onValueChange = onHintsChange,
            placeholder   = { Text("Soft gradient background, large font…", color = MaterialTheme.colorScheme.onSurfaceVariant) },
            modifier      = Modifier.fillMaxWidth(),
            maxLines      = 3,
            colors        = jarvisOutlinedTextFieldColors(),
        )

        SectionTitle("Type")
        FlowRow(horizontalArrangement = Arrangement.spacedBy(Space.sm)) {
            AppType.entries.forEach { t ->
                FilterChip(
                    selected = ui.selectedType == t,
                    onClick  = { onTypeChange(t) },
                    label    = { Text(t.label) },
                    colors   = FilterChipDefaults.filterChipColors(
                        selectedContainerColor = MaterialTheme.colorScheme.primary,
                        selectedLabelColor     = MaterialTheme.colorScheme.onPrimary,
                    ),
                )
            }
        }

        Spacer(Modifier.height(Space.sm))

        Button(
            onClick  = if (ui.isGenerating) onCancel else onGenerate,
            enabled  = if (ui.isGenerating) true else ui.canGenerate,
            modifier = Modifier.fillMaxWidth(),
            colors   = ButtonDefaults.buttonColors(
                containerColor = if (ui.isGenerating) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary,
                contentColor   = if (ui.isGenerating) Color.White else MaterialTheme.colorScheme.onPrimary,
            ),
        ) {
            if (ui.isGenerating) {
                CircularProgressIndicator(color = Color.White, strokeWidth = 2.dp, modifier = Modifier.padding(end = Space.sm))
                Icon(Icons.Default.Stop, contentDescription = null)
                Text("Cancel")
            } else {
                Icon(Icons.Default.Code, contentDescription = null, modifier = Modifier.padding(end = Space.sm))
                Text("Generate")
            }
        }

        if (ui.generationLog.isNotEmpty()) {
            GenerationLogStream(lines = ui.generationLog)
        }
    }
}
```

- [ ] **Step 2: Compile**

Run: `cd src/android && ./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Commit**

```bash
git add src/android/app/src/main/java/com/jarvis/android/presentation/builder/pane/DescribePane.kt
git commit -m "feat(app-builder): add DescribePane composable"
```

---

### Task 20: CodePane

**Files:**
- Create: `src/android/app/src/main/java/com/jarvis/android/presentation/builder/pane/CodePane.kt`

- [ ] **Step 1: Write the file**

```kotlin
package com.jarvis.android.presentation.builder.pane

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Build
import androidx.compose.material.icons.filled.Code
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.unit.dp
import com.jarvis.android.presentation.builder.BuilderUiState
import com.jarvis.android.presentation.builder.components.CodeViewer
import com.jarvis.android.presentation.builder.components.EmptyState
import com.jarvis.android.presentation.builder.components.Space

@Composable
fun CodePane(
    ui:         BuilderUiState,
    onEdit:     () -> Unit,
    onBuild:    () -> Unit,
    modifier:   Modifier = Modifier,
) {
    if (ui.sourceCode.isBlank()) {
        EmptyState(
            icon     = Icons.Default.Code,
            title    = "No code yet",
            helper   = "Generate source from the Describe pane.",
        )
        return
    }

    val clipboard = LocalClipboardManager.current

    Column(modifier.fillMaxSize().padding(Space.lg)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                text     = "Source",
                style    = MaterialTheme.typography.labelMedium,
                color    = MaterialTheme.colorScheme.primary,
                modifier = Modifier.weight(1f),
            )
            OutlinedButton(
                onClick = { clipboard.setText(AnnotatedString(ui.sourceCode)) },
                modifier = Modifier.padding(end = Space.sm),
            ) {
                Icon(Icons.Default.ContentCopy, contentDescription = null, modifier = Modifier.padding(end = 4.dp))
                Text("Copy")
            }
            OutlinedButton(onClick = onEdit) {
                Icon(Icons.Default.Edit, contentDescription = null, modifier = Modifier.padding(end = 4.dp))
                Text("Edit")
            }
        }
        Spacer(Modifier.height(Space.sm))
        Box(Modifier.weight(1f)) {
            ui.selectedProject?.let { project ->
                CodeViewer(source = ui.sourceCode, type = project.type, modifier = Modifier.fillMaxSize())
            }
        }
        Spacer(Modifier.height(Space.md))
        Button(
            onClick  = onBuild,
            enabled  = ui.canBuild,
            modifier = Modifier.fillMaxWidth(),
            colors   = ButtonDefaults.buttonColors(
                containerColor = MaterialTheme.colorScheme.primary,
                contentColor   = MaterialTheme.colorScheme.onPrimary,
            ),
        ) {
            Icon(Icons.Default.Build, contentDescription = null, modifier = Modifier.padding(end = Space.sm))
            Text("Build")
        }
    }
}
```

- [ ] **Step 2: Compile**

Run: `cd src/android && ./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Commit**

```bash
git add src/android/app/src/main/java/com/jarvis/android/presentation/builder/pane/CodePane.kt
git commit -m "feat(app-builder): add CodePane with Edit/Copy/Build actions"
```

---

### Task 21: RunPane

**Files:**
- Create: `src/android/app/src/main/java/com/jarvis/android/presentation/builder/pane/RunPane.kt`

- [ ] **Step 1: Write the file**

```kotlin
package com.jarvis.android.presentation.builder.pane

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Build
import androidx.compose.material.icons.filled.Rocket
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import com.jarvis.android.presentation.builder.BuilderUiState
import com.jarvis.android.presentation.builder.components.BuildResultCard
import com.jarvis.android.presentation.builder.components.EmptyState
import com.jarvis.android.presentation.builder.components.Space

@Composable
fun RunPane(
    ui:        BuilderUiState,
    onRun:     () -> Unit,
    onRetry:   () -> Unit,
    modifier:  Modifier = Modifier,
) {
    val result = ui.lastBuildResult

    if (result == null) {
        EmptyState(
            icon   = Icons.Default.Build,
            title  = "Not built yet",
            helper = "Build your project from the Code pane first.",
        )
        return
    }

    Column(modifier.fillMaxSize().padding(Space.lg)) {
        BuildResultCard(
            result  = result,
            onRun   = if (ui.canRun) onRun else null,
            onRetry = if (!result.success) onRetry else null,
        )
        Spacer(Modifier.height(Space.lg))
        // Preview panel placeholder — see spec §7 "No live WebView preview"
        EmptyState(
            icon   = Icons.Default.Rocket,
            title  = "Preview",
            helper = "Tap Run to launch the built app in its native runner.",
        )
    }
}
```

- [ ] **Step 2: Compile**

Run: `cd src/android && ./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Commit**

```bash
git add src/android/app/src/main/java/com/jarvis/android/presentation/builder/pane/RunPane.kt
git commit -m "feat(app-builder): add RunPane with BuildResultCard + placeholder preview"
```

---

## Phase 4 — Screens

### Task 22: AppBuilderProjectScreen (assembles segmented control + three panes)

**Files:**
- Create: `src/android/app/src/main/java/com/jarvis/android/presentation/builder/AppBuilderProjectScreen.kt`

- [ ] **Step 1: Write the file**

```kotlin
package com.jarvis.android.presentation.builder

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.jarvis.android.presentation.builder.components.BuilderSegmentedControl
import com.jarvis.android.presentation.builder.components.Space
import com.jarvis.android.presentation.builder.pane.CodePane
import com.jarvis.android.presentation.builder.pane.DescribePane
import com.jarvis.android.presentation.builder.pane.RunPane

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AppBuilderProjectScreen(
    projectId:     String,
    vm:            AppBuilderViewModel,
    onBack:        () -> Unit,
    onEditCode:    () -> Unit,
    onLaunchApp:   (path: String) -> Unit,
) {
    val ui by vm.ui.collectAsState()

    // Hydrate state from the passed projectId the first time.
    LaunchedEffect(projectId) { vm.selectProjectById(projectId) }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = { Text(ui.selectedProject?.name ?: "Loading…", style = MaterialTheme.typography.titleLarge) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.Default.ArrowBack, contentDescription = "Back", tint = MaterialTheme.colorScheme.primary)
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.background),
            )
        },
    ) { padding ->
        Column(Modifier.padding(padding).fillMaxSize()) {
            BuilderSegmentedControl(
                selected = ui.selectedSegment,
                onSelect = vm::selectSegment,
                modifier = Modifier.padding(horizontal = Space.lg, vertical = Space.sm),
            )
            Spacer(Modifier.height(Space.xs))
            Box(Modifier.fillMaxSize()) {
                when (ui.selectedSegment) {
                    ProjectSegment.DESCRIBE -> DescribePane(
                        ui                  = ui,
                        onDescriptionChange = vm::setDescription,
                        onHintsChange       = vm::setExtraHints,
                        onTypeChange        = vm::setAppType,
                        onGenerate          = vm::generateCode,
                        onCancel            = vm::cancelGeneration,
                    )
                    ProjectSegment.CODE -> CodePane(
                        ui      = ui,
                        onEdit  = onEditCode,
                        onBuild = vm::buildCurrentProject,
                    )
                    ProjectSegment.RUN -> RunPane(
                        ui      = ui,
                        onRun   = { ui.launchPath?.let(onLaunchApp) },
                        onRetry = vm::buildCurrentProject,
                    )
                }
            }
        }
    }
}
```

- [ ] **Step 2: Compile**

Run: `cd src/android && ./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Commit**

```bash
git add src/android/app/src/main/java/com/jarvis/android/presentation/builder/AppBuilderProjectScreen.kt
git commit -m "feat(app-builder): add AppBuilderProjectScreen with segmented panes"
```

---

### Task 23: AppBuilderTemplatesScreen

**Files:**
- Create: `src/android/app/src/main/java/com/jarvis/android/presentation/builder/AppBuilderTemplatesScreen.kt`

- [ ] **Step 1: Write the file**

```kotlin
package com.jarvis.android.presentation.builder

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.SearchOff
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import com.jarvis.android.domain.model.TemplateCategory
import com.jarvis.android.presentation.builder.components.EmptyState
import com.jarvis.android.presentation.builder.components.Space
import com.jarvis.android.presentation.builder.components.TemplateCard

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun AppBuilderTemplatesScreen(
    vm:                AppBuilderViewModel,
    onBack:            () -> Unit,
    onProjectOpened:   (projectId: String) -> Unit,
) {
    val ui by vm.ui.collectAsState()

    LaunchedEffect(Unit) {
        vm.navEvents.collect { event ->
            when (event) {
                is AppBuilderNav.OpenProject -> onProjectOpened(event.projectId)
            }
        }
    }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = { Text("Start from template", style = MaterialTheme.typography.titleLarge) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.Default.ArrowBack, contentDescription = "Back", tint = MaterialTheme.colorScheme.primary)
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.background),
            )
        },
    ) { padding ->
        Column(Modifier.padding(padding).fillMaxSize()) {
            FlowRow(
                modifier = Modifier.fillMaxWidth().padding(horizontal = Space.md, vertical = Space.sm),
                horizontalArrangement = Arrangement.spacedBy(Space.sm),
            ) {
                FilterChip(
                    selected = ui.templateFilter == null,
                    onClick  = { vm.setTemplateFilter(null) },
                    label    = { Text("All") },
                    colors   = FilterChipDefaults.filterChipColors(
                        selectedContainerColor = MaterialTheme.colorScheme.primary,
                        selectedLabelColor     = MaterialTheme.colorScheme.onPrimary,
                    ),
                )
                TemplateCategory.entries.forEach { cat ->
                    FilterChip(
                        selected = ui.templateFilter == cat,
                        onClick  = { vm.setTemplateFilter(cat) },
                        label    = { Text(cat.label) },
                        colors   = FilterChipDefaults.filterChipColors(
                            selectedContainerColor = MaterialTheme.colorScheme.primary,
                            selectedLabelColor     = MaterialTheme.colorScheme.onPrimary,
                        ),
                    )
                }
            }

            if (ui.filteredTemplates.isEmpty()) {
                EmptyState(
                    icon   = Icons.Default.SearchOff,
                    title  = "No templates match this filter",
                    helper = "Try a different category or pick All.",
                )
                return@Column
            }

            LazyColumn(
                modifier = Modifier.fillMaxSize().padding(horizontal = Space.lg),
                verticalArrangement = Arrangement.spacedBy(Space.md),
            ) {
                items(ui.filteredTemplates, key = { it.id }) { template ->
                    TemplateCard(
                        template = template,
                        onApply  = { vm.startProjectFromTemplate(template) },
                    )
                }
            }
        }
    }
}
```

- [ ] **Step 2: Compile**

Run: `cd src/android && ./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Commit**

```bash
git add src/android/app/src/main/java/com/jarvis/android/presentation/builder/AppBuilderTemplatesScreen.kt
git commit -m "feat(app-builder): add AppBuilderTemplatesScreen gallery"
```

---

### Task 24: AppBuilderHomeScreen

**Files:**
- Create: `src/android/app/src/main/java/com/jarvis/android/presentation/builder/AppBuilderHomeScreen.kt`

- [ ] **Step 1: Write the file**

```kotlin
package com.jarvis.android.presentation.builder

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Code
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import com.jarvis.android.domain.model.BuildStatus
import com.jarvis.android.presentation.builder.components.EmptyState
import com.jarvis.android.presentation.builder.components.ProjectCard
import com.jarvis.android.presentation.builder.components.Space
import com.jarvis.android.presentation.builder.components.jarvisOutlinedTextFieldColors
import com.jarvis.android.presentation.builder.sheet.NewProjectSheet

/**
 * Maps the persisted [BuildStatus] on an [com.jarvis.android.domain.model.AppProject]
 * to a [BuildPhase] so the card can render a pill without the screen knowing
 * about the VM's transient build/generate flags.
 */
private fun phaseFromStatus(status: BuildStatus): BuildPhase = when (status) {
    BuildStatus.IDLE       -> BuildPhase.IDLE
    BuildStatus.GENERATING -> BuildPhase.GENERATING
    BuildStatus.BUILDING   -> BuildPhase.BUILDING
    BuildStatus.READY      -> BuildPhase.READY
    BuildStatus.FAILED     -> BuildPhase.FAILED
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AppBuilderHomeScreen(
    vm:                  AppBuilderViewModel,
    onBack:              () -> Unit,
    onOpenProject:       (projectId: String) -> Unit,
    onGoToTemplates:     () -> Unit,
) {
    val ui by vm.ui.collectAsState()

    // Collect nav events (e.g. from the New Project sheet creating a project).
    LaunchedEffect(Unit) {
        vm.navEvents.collect { event ->
            when (event) {
                is AppBuilderNav.OpenProject -> onOpenProject(event.projectId)
            }
        }
    }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = { Text("App Builder", style = MaterialTheme.typography.titleLarge) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.Default.ArrowBack, contentDescription = "Back", tint = MaterialTheme.colorScheme.primary)
                    }
                },
                actions = {
                    IconButton(onClick = { vm.setProjectName(""); vm.setDescription(""); vm.showNewProjectSheet() }) {
                        Icon(Icons.Default.Add, contentDescription = "New project", tint = MaterialTheme.colorScheme.primary)
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.background),
            )
        },
    ) { padding ->
        Box(Modifier.padding(padding).fillMaxSize()) {
            if (ui.projects.isEmpty()) {
                EmptyState(
                    icon     = Icons.Default.Code,
                    title    = "No projects yet",
                    helper   = "Start your first app.",
                    ctaLabel = "+ New project",
                    onCta    = { vm.setProjectName(""); vm.setDescription(""); vm.showNewProjectSheet() },
                )
            } else {
                LazyColumn(
                    modifier = Modifier.fillMaxSize().padding(horizontal = Space.lg, vertical = Space.sm),
                    verticalArrangement = Arrangement.spacedBy(Space.md),
                ) {
                    items(ui.projects, key = { it.id }) { project ->
                        // Derive per-card phase from the persisted domain status,
                        // then override with the VM's live transient phase ONLY
                        // for the project currently being operated on — so other
                        // cards keep showing their persisted READY/IDLE state
                        // while one project is generating or building.
                        val cardPhase = if (ui.selectedProject?.id == project.id && ui.buildPhase != BuildPhase.IDLE)
                            ui.buildPhase
                        else phaseFromStatus(project.buildStatus)

                        ProjectCard(
                            project   = project,
                            phase     = cardPhase,
                            onClick   = { onOpenProject(project.id) },
                            onRename  = { vm.showRenameDialog(project) },
                            onDelete  = { vm.showDeleteConfirm(project) },
                        )
                    }
                }
            }
        }
    }

    if (ui.showNewProjectSheet) {
        NewProjectSheet(
            name                = ui.projectName,
            description         = ui.description,
            selectedType        = ui.selectedType,
            onNameChange        = vm::setProjectName,
            onDescriptionChange = vm::setDescription,
            onTypeChange        = vm::setAppType,
            onCreate            = vm::createNewProject,
            onStartFromTemplate = { vm.hideNewProjectSheet(); onGoToTemplates() },
            onDismiss           = vm::hideNewProjectSheet,
            canCreate           = ui.projectName.isNotBlank(),
        )
    }

    if (ui.showRenameDialog) {
        AlertDialog(
            onDismissRequest = vm::hideRenameDialog,
            containerColor   = MaterialTheme.colorScheme.surface,
            title            = { Text("Rename project", color = MaterialTheme.colorScheme.primary) },
            text             = {
                OutlinedTextField(
                    value         = ui.projectName,
                    onValueChange = vm::setProjectName,
                    label         = { Text("New name") },
                    singleLine    = true,
                    colors        = jarvisOutlinedTextFieldColors(),
                )
            },
            confirmButton = {
                Button(
                    onClick  = vm::renameCurrentProject,
                    enabled  = ui.projectName.isNotBlank(),
                    colors   = ButtonDefaults.buttonColors(
                        containerColor = MaterialTheme.colorScheme.primary,
                        contentColor   = MaterialTheme.colorScheme.onPrimary,
                    ),
                ) { Text("Rename") }
            },
            dismissButton = { TextButton(onClick = vm::hideRenameDialog) { Text("Cancel") } },
        )
    }

    if (ui.showDeleteConfirm) {
        val project = ui.projectToActOn
        if (project != null) {
            AlertDialog(
                onDismissRequest = vm::hideDeleteConfirm,
                containerColor   = MaterialTheme.colorScheme.surface,
                title            = { Text("Delete project", color = MaterialTheme.colorScheme.error) },
                text             = { Text("Delete \"${project.name}\" and all its build artifacts? This cannot be undone.") },
                confirmButton = {
                    Button(
                        onClick = { vm.deleteProject(project) },
                        colors  = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error),
                    ) { Text("Delete") }
                },
                dismissButton = { TextButton(onClick = vm::hideDeleteConfirm) { Text("Cancel") } },
            )
        }
    }

    ui.errorMessage?.let { msg ->
        AlertDialog(
            onDismissRequest = vm::clearError,
            containerColor   = MaterialTheme.colorScheme.surface,
            title            = { Text("Error", color = MaterialTheme.colorScheme.error) },
            text             = { Text(msg) },
            confirmButton    = { TextButton(onClick = vm::clearError) { Text("OK") } },
        )
    }
}
```

- [ ] **Step 2: Compile**

Run: `cd src/android && ./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Commit**

```bash
git add src/android/app/src/main/java/com/jarvis/android/presentation/builder/AppBuilderHomeScreen.kt
git commit -m "feat(app-builder): add AppBuilderHomeScreen with card list + sheet + dialogs"
```

---

### Task 25: CodeEditorScreen

**Files:**
- Create: `src/android/app/src/main/java/com/jarvis/android/presentation/builder/CodeEditorScreen.kt`

- [ ] **Step 1: Write the file**

```kotlin
package com.jarvis.android.presentation.builder

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.sp
import com.jarvis.android.core.designsystem.LocalJarvisColors

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CodeEditorScreen(
    vm:      AppBuilderViewModel,
    onClose: () -> Unit,
) {
    val ui by vm.ui.collectAsState()
    val jarvis = LocalJarvisColors.current

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = { Text(ui.selectedProject?.name ?: "Edit", style = MaterialTheme.typography.titleMedium) },
                navigationIcon = {
                    IconButton(onClick = onClose) {
                        Icon(Icons.Default.Close, contentDescription = "Close", tint = MaterialTheme.colorScheme.primary)
                    }
                },
                actions = {
                    TextButton(onClick = onClose) { Text("Save", color = MaterialTheme.colorScheme.primary) }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.background),
            )
        },
    ) { padding ->
        Column(Modifier.padding(padding).fillMaxSize().imePadding()) {
            OutlinedTextField(
                value         = ui.sourceCode,
                onValueChange = vm::setSourceCode,
                modifier      = Modifier.fillMaxSize(),
                textStyle     = TextStyle(
                    color      = jarvis.terminalText,
                    fontFamily = FontFamily.Monospace,
                    fontSize   = 13.sp,
                    lineHeight = 18.sp,
                ),
                colors = OutlinedTextFieldDefaults.colors(
                    focusedContainerColor   = jarvis.codeBg,
                    unfocusedContainerColor = jarvis.codeBg,
                    focusedBorderColor      = jarvis.codeBorder,
                    unfocusedBorderColor    = jarvis.codeBorder,
                    cursorColor             = MaterialTheme.colorScheme.primary,
                ),
            )
        }
    }
}
```

Note: `vm.setSourceCode` persists to in-memory state. Hitting Save just closes — the generate flow is the usual persistence path. Pending improvement (out of scope): wire Save to `UpdateSourceCodeUseCase` directly.

- [ ] **Step 2: Compile**

Run: `cd src/android && ./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Commit**

```bash
git add src/android/app/src/main/java/com/jarvis/android/presentation/builder/CodeEditorScreen.kt
git commit -m "feat(app-builder): add CodeEditorScreen full-screen editor"
```

---

## Phase 5 — Navigation wiring

### Task 26: Nested Screen routes

**Files:**
- Modify: `src/android/app/src/main/java/com/jarvis/android/navigation/Screen.kt`

- [ ] **Step 1: Nest the AppBuilder route object**

Replace the existing `data object AppBuilder : Screen("app_builder")` with:

```kotlin
    /** On-device app builder — nested graph. */
    data object AppBuilder : Screen("app_builder") {

        /** Project list (default destination). */
        data object Home : Screen("app_builder/home")

        /** Project detail — segmented Describe/Code/Run. Arg: projectId. */
        data object Project : Screen("app_builder/project/{projectId}") {
            const val ARG_PROJECT_ID = "projectId"
            fun route(projectId: String) = "app_builder/project/$projectId"
        }

        /** Full-screen code editor. Arg: projectId. */
        data object CodeEditor : Screen("app_builder/project/{projectId}/code-edit") {
            const val ARG_PROJECT_ID = "projectId"
            fun route(projectId: String) = "app_builder/project/$projectId/code-edit"
        }

        /** Template gallery. */
        data object Templates : Screen("app_builder/templates")
    }
```

- [ ] **Step 2: Compile (expect NavGraph to still reference the old route)**

Run: `cd src/android && ./gradlew :app:compileDebugKotlin`
Expected: may compile because `Screen.AppBuilder.route` still evaluates to `"app_builder"`; if the NavGraph refers to `Screen.AppBuilder.route` directly for its composable(), that still resolves.

- [ ] **Step 3: Commit**

```bash
git add src/android/app/src/main/java/com/jarvis/android/navigation/Screen.kt
git commit -m "feat(navigation): nest AppBuilder routes for Home/Project/Templates/CodeEditor"
```

---

### Task 27: NavGraph — nested `navigation { ... }` sub-graph

**Files:**
- Modify: `src/android/app/src/main/java/com/jarvis/android/navigation/JarvisNavGraph.kt`

- [ ] **Step 1: Replace the single composable(AppBuilder.route) with a nested graph**

Locate the block in `JarvisNavGraph.kt`:

```kotlin
        composable(Screen.AppBuilder.route) {
            AppBuilderScreen(
                onBack      = { navController.popBackStack() },
                onLaunchApp = { path ->
                    navController.navigate(Screen.Terminal.route)
                },
            )
        }
```

Replace the whole block with:

```kotlin
        navigation(
            startDestination = Screen.AppBuilder.Home.route,
            route            = Screen.AppBuilder.route,
        ) {
            composable(Screen.AppBuilder.Home.route) { backStackEntry ->
                val vm: AppBuilderViewModel = hiltViewModel(
                    remember(backStackEntry) { navController.getBackStackEntry(Screen.AppBuilder.route) },
                )
                AppBuilderHomeScreen(
                    vm               = vm,
                    onBack           = { navController.popBackStack() },
                    onOpenProject    = { id -> navController.navigate(Screen.AppBuilder.Project.route(id)) },
                    onGoToTemplates  = { navController.navigate(Screen.AppBuilder.Templates.route) },
                )
            }

            composable(
                route     = Screen.AppBuilder.Project.route,
                arguments = listOf(
                    navArgument(Screen.AppBuilder.Project.ARG_PROJECT_ID) { type = NavType.StringType },
                ),
            ) { backStackEntry ->
                val vm: AppBuilderViewModel = hiltViewModel(
                    remember(backStackEntry) { navController.getBackStackEntry(Screen.AppBuilder.route) },
                )
                val projectId = backStackEntry.arguments?.getString(Screen.AppBuilder.Project.ARG_PROJECT_ID) ?: return@composable
                AppBuilderProjectScreen(
                    projectId  = projectId,
                    vm         = vm,
                    onBack     = { navController.popBackStack() },
                    onEditCode = { navController.navigate(Screen.AppBuilder.CodeEditor.route(projectId)) },
                    onLaunchApp = { path -> navController.navigate(Screen.Terminal.route) },
                )
            }

            composable(
                route     = Screen.AppBuilder.CodeEditor.route,
                arguments = listOf(
                    navArgument(Screen.AppBuilder.CodeEditor.ARG_PROJECT_ID) { type = NavType.StringType },
                ),
            ) { backStackEntry ->
                val vm: AppBuilderViewModel = hiltViewModel(
                    remember(backStackEntry) { navController.getBackStackEntry(Screen.AppBuilder.route) },
                )
                CodeEditorScreen(vm = vm, onClose = { navController.popBackStack() })
            }

            composable(Screen.AppBuilder.Templates.route) { backStackEntry ->
                val vm: AppBuilderViewModel = hiltViewModel(
                    remember(backStackEntry) { navController.getBackStackEntry(Screen.AppBuilder.route) },
                )
                AppBuilderTemplatesScreen(
                    vm              = vm,
                    onBack          = { navController.popBackStack() },
                    onProjectOpened = { id ->
                        // Pop Templates from the back stack so back from Project goes to Home
                        navController.popBackStack()
                        navController.navigate(Screen.AppBuilder.Project.route(id))
                    },
                )
            }
        }
```

- [ ] **Step 2: Add the required imports at the top of `JarvisNavGraph.kt`**

```kotlin
import androidx.compose.runtime.remember
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.navigation.NavType
import androidx.navigation.compose.navigation
import androidx.navigation.navArgument
import com.jarvis.android.presentation.builder.AppBuilderHomeScreen
import com.jarvis.android.presentation.builder.AppBuilderProjectScreen
import com.jarvis.android.presentation.builder.AppBuilderTemplatesScreen
import com.jarvis.android.presentation.builder.AppBuilderViewModel
import com.jarvis.android.presentation.builder.CodeEditorScreen
```

Remove the now-unused `import com.jarvis.android.presentation.builder.AppBuilderScreen`.

- [ ] **Step 3: Compile**

Run: `cd src/android && ./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL. If `AppBuilderScreen` is still referenced by the import, remove it.

- [ ] **Step 4: Commit**

```bash
git add src/android/app/src/main/java/com/jarvis/android/navigation/JarvisNavGraph.kt
git commit -m "feat(navigation): nest App Builder sub-graph with shared VM scope"
```

---

### Task 28: Delete old `AppBuilderScreen.kt`

**Files:**
- Delete: `src/android/app/src/main/java/com/jarvis/android/presentation/builder/AppBuilderScreen.kt`
- Modify: `src/android/app/src/main/java/com/jarvis/android/presentation/builder/AppBuilderViewModel.kt`

- [ ] **Step 1: Delete the old screen file**

Run: `rm src/android/app/src/main/java/com/jarvis/android/presentation/builder/AppBuilderScreen.kt`

- [ ] **Step 2: Remove the deprecated `applyTemplate` from the VM**

The legacy `applyTemplate(template)` was kept only for the old screen. Delete that function from `AppBuilderViewModel.kt`.

- [ ] **Step 3: Compile**

Run: `cd src/android && ./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL. No unresolved references.

- [ ] **Step 4: Run the full unit-test suite**

Run: `cd src/android && ./gradlew :app:testDebugUnitTest`
Expected: PASS across existing tests + the three new VM test files.

- [ ] **Step 5: Commit**

```bash
git add -u src/android/app/src/main/java/com/jarvis/android/presentation/builder/
git commit -m "refactor(app-builder): remove legacy AppBuilderScreen and applyTemplate"
```

---

## Phase 6 — Manual smoke + previews

### Task 29: Manual smoke checklist

- [ ] **Step 1: Install the app on an emulator / device**

Run: `cd src/android && ./gradlew :app:installDebug`
Expected: APK installed on the connected device.

- [ ] **Step 2: Walk the golden path**

Launch JARVIS → navigate to App Builder.

1. Home should show "No projects yet" empty state with `+ New project` CTA.
2. Tap **+** in TopBar → bottom sheet appears with Name field focused.
3. Enter name = "Smoke App", description = "Lock-screen weather tile." Leave type = Web.
4. Tap **Create**. The sheet dismisses, navigation goes to Project/Describe.
5. Confirm description and type are pre-filled. Tap **Generate**. Log appears in `GenerationLogStream`.
6. When log finishes and code appears, switch to **Code** segment. Code is shown, syntax-colored.
7. Tap **Build**. Wait for completion.
8. Switch to **Run** segment. `BuildResultCard` shows green banner. Tap **Run**; the Terminal screen opens.
9. Back to Home. Long-press/tap `⋯` on the project → **Rename** → rename to "Smoke App 2" → confirm in list.
10. `⋯` → **Delete** → confirm. List goes back to empty state.

- [ ] **Step 3: Walk the template path**

1. Home `+` → sheet → tap **Start from template →**. Navigate to template gallery.
2. Tap a template → new project created, navigation goes to Project/Describe with name/description pre-filled.
3. Confirm code is already present under the Code tab (no Generate needed).
4. Back — ensure back stack is Home → Project (Templates popped).

- [ ] **Step 4: Rotate device on each screen**

Rotate on Home, on each pane of Project, on Templates, on the bottom sheet. No state loss.

- [ ] **Step 5: Light-mode smoke**

Toggle system to light mode. Re-walk Home and Project. Every component reads correctly — no dark rectangles, text stays readable.

- [ ] **Step 6: No commit required for this task**

Smoke-only. Proceed if everything passes; file a follow-up ticket otherwise.

---

### Task 30: Final tidy and overall commit

- [ ] **Step 1: Run static checks / KtLint if the project has them**

Run: `cd src/android && ./gradlew :app:lintDebug` (if configured).
Expected: no new lint warnings introduced by this work.

- [ ] **Step 2: Run all unit tests once more**

Run: `cd src/android && ./gradlew :app:testDebugUnitTest`
Expected: all PASS.

- [ ] **Step 3: If anything comes up above, fix and commit with `chore(app-builder): post-smoke cleanup` as the message.**

---

## Success criteria

- All unit tests pass.
- `./gradlew :app:compileDebugKotlin` succeeds.
- Manual smoke checklist in Task 29 passes end-to-end on at least one device or emulator.
- No hardcoded `Color(0xFF…)` remains in any file under `presentation/builder/` (new code). The only exception is the category-gradient palette in `TemplateCard.kt` §17, which encodes category identity, not theme.
- `presentation/builder/AppBuilderScreen.kt` no longer exists.
- Light-mode renders correctly on every new screen without any code changes.
