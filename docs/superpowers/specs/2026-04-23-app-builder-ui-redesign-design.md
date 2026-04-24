# App Builder UI redesign — design spec

**Status:** Draft
**Date:** 2026-04-23
**Scope:** Android app, `com.jarvis.android.presentation.builder` and related theme/component layers.
**Out of scope:** Domain models, UseCases, Repository, AppBuildEngine, AppCodeGenerator. These stay untouched.

---

## 1. Problem

The current `AppBuilderScreen` stuffs three concerns (Projects list, Project builder, Template gallery) into one screen via a `TabRow`. It hardcodes colors that bypass the project's Material3 theme, uses fixed dp/sp values with no rhythm, and mixes a status pill, type chip, generation log, build result, and code editor in one long scroll. The result reads as a proof of concept, not a product.

This redesign rebuilds the feature as three stack-navigated screens that use the existing theme tokens, give each part of the `describe → generate → build → run` loop its own focused surface, and replace the Template tab with a blank-first bottom sheet plus an on-demand template gallery.

## 2. Decisions already locked

1. **Visual direction:** Linear / Vercel — calm editorial dashboard, subtle status, single primary action per surface.
2. **Detail screen layout:** segmented control with three panes — `Describe / Code / Run`.
3. **Create flow:** tap `+` on Home opens a `ModalBottomSheet` (name + one-line intent + type + `Create`). A ghost-style `Start from template →` link in the sheet routes to a full template gallery screen.
4. **Domain layer, VM use cases, build engine:** unchanged.

## 3. Navigation & screens

### 3.1 Nav graph

```
app_builder_graph (route: "app_builder", startDestination: "app_builder/home")
    ├─ Home                 (route: "app_builder/home",            default)
    ├─ Project              (route: "app_builder/project/{id}")
    ├─ Project.CodeEditor   (route: "app_builder/project/{id}/code-edit")
    └─ Templates            (route: "app_builder/templates")
```

Existing entry points that navigate to `"app_builder"` continue to work — the graph's `startDestination` resolves them to `app_builder/home` without changing any caller.

A single graph-scoped `AppBuilderViewModel` is resolved via
`hiltViewModel(navBackStackEntry = navController.getBackStackEntry("app_builder"))`
so all four destinations share state.

### 3.2 User flows

```
Home ─(tap +)──► NewProjectSheet (ModalBottomSheet)
   │                   │
   │                   ├─(Create)─────────► Project(id=new).Describe
   │                   └─(Start from tpl)─► Templates
   │                                            └─(tap)─► Project(id=new, prefilled).Describe
   │
   ├─(tap card)──► Project(id).Describe
   │
   └─(⋯ on card)─► inline menu → Rename dialog / Delete confirm dialog
```

### 3.3 Screen roles

| Screen | Purpose | Empty state |
|---|---|---|
| `AppBuilderHomeScreen` | Dashboard of projects. One primary action (`+`). The list is the hero. | "No projects yet. Start your first app." + big `+ New project` button. |
| `NewProjectSheet` | Name, one-line description, type chip row, `Create`, ghost `Start from template →`. ~60% height, keyboard-aware. | n/a |
| `AppBuilderTemplatesScreen` | Template gallery with category filter. Tap a template → pre-fills a new project and jumps to Project/Describe. | "No templates match" when filter returns empty. |
| `AppBuilderProjectScreen.Describe` | Editable title in TopBar. Description textarea, extra hints textarea, type chip row. Primary button at bottom: `Generate`. Inline `GenerationLogStream` below when active. | Description blank → `Generate` disabled. |
| `AppBuilderProjectScreen.Code` | Read-mostly `CodeViewer` with basic token coloring. Top actions: `Edit`, `Copy`. Primary bottom button: `Build`. Tapping `Edit` opens `CodeEditorScreen` full-screen. | "Generate first from Describe" + button that switches segment to Describe. |
| `AppBuilderProjectScreen.Run` | Shows `lastBuildResult` in a `BuildResultCard`. If READY: `Run` primary button + preview panel placeholder (WebView thumb for web, "Open in terminal" for shell/python). If FAILED: error card + `Retry build`. If IDLE: "Build first from Code." | Same as IDLE branch. |
| `RenameDialog`, `DeleteConfirmDialog` | Unchanged. Reached from the overflow menu on a `ProjectCard`. | n/a |

The old three-tab `TabRow` is removed.

## 4. Components

All new components live in `com.jarvis.android.presentation.builder.components/`. Every one uses `MaterialTheme.colorScheme.*` and `LocalJarvisTypography.current` — no hardcoded hex.

### 4.1 New

| Component | Purpose |
|---|---|
| `ProjectCard` | Home list row: name (`titleMedium`), description (`bodySmall`, 2-line clamp), `StatusPill`, `AppTypePill`, inline hairline progress when GENERATING/BUILDING, trailing overflow `⋯`. Whole-card click → Project screen. |
| `StatusPill` | READY = `successGreen`, BUILDING = `warningAmber` + tiny spinner, GENERATING = primary blue + tiny spinner, FAILED = `error`, IDLE = dim (no fill). Uses `BuildPhase` enum. |
| `AppTypePill` | Web = blue, Shell = green, Python = lavender (tertiary). `labelSmall`. |
| `BuilderSegmentedControl` | Three-segment Describe / Code / Run. Rounded 10dp container, active segment gets `surfaceVariant` surface + primary-color label. |
| `NewProjectSheet` | `ModalBottomSheet` with name + description + `FilterChip` row for type, `Create` button, divider, ghost `Start from template →`. `skipPartiallyExpanded = true`. Focuses the name field on open. |
| `TemplateCard` (redesigned) | Category-gradient thumbnail placeholder, name (`titleSmall`), 2-line description, `AppTypePill`, tags. Full-card tap applies and navigates. |
| `GenerationLogStream` | Auto-scrolling monospace log inside a `codeBg` panel. Dim older lines; newest at full opacity. `terminalBody` type. |
| `BuildResultCard` | Success/failure banner: icon, `titleSmall` headline, secondary text, optional action button. Color from result. |
| `EmptyState` | Centered icon + title + one-line helper + primary CTA. Used for: no projects, code not generated, not built yet, templates filtered to empty. |
| `CodeViewer` | `LazyColumn` of line-keyed `Text` items with regex-based token coloring for HTML / Bash / Python. Read-only. |
| `CodeEditorScreen` | Full-screen focused edit. TopBar Cancel / Save. Monospace editor with inline line numbers, keyboard-safe padding. New nav destination `app_builder/project/{id}/code-edit`. |

### 4.2 Reused

- `SectionTitle` from `SettingsScreen.kt` — promoted to shared `components/` because multiple screens now use it.
- A new shared `JarvisTextFieldDefaults` replaces the local `jarvisTextFieldColors()` copy currently in `AppBuilderScreen.kt`.

### 4.3 Removed / replaced

- `BuildStatusBadge` (local in `AppBuilderScreen.kt`) → replaced by `StatusPill`.
- Local `AppTypeChip` → replaced by `AppTypePill`.
- Local `jarvisTextFieldColors()` → moved to shared `JarvisTextFieldDefaults`.
- Three-tab `TabRow` scaffolding → gone.
- `AppBuilderScreen.kt` single-file composite → split into `AppBuilderHomeScreen.kt`, `AppBuilderProjectScreen.kt`, `AppBuilderTemplatesScreen.kt`.

## 5. Design tokens

All tokens are drawn from the existing theme (`core/designsystem/Color.kt`, `Typography.kt`). Nothing new is introduced.

### 5.1 Colors

| Role | Token | Example use |
|---|---|---|
| Page background | `colorScheme.background` (`#0A0A0A`) | All screens |
| Card / sheet surface | `colorScheme.surface` (`#141414`) | `ProjectCard`, `NewProjectSheet`, `TemplateCard` |
| Elevated / pressed | `colorScheme.surfaceVariant` (`#1E1E1E`) | Active segment, pressed card |
| Primary accent | `colorScheme.primary` (`#1E7FFF`) | Create, Generate, Build, Run, active segment label |
| On-primary | `colorScheme.onPrimary` (`#FFFFFF`) | Label in primary buttons |
| Muted accent border | `LocalJarvisColors.current.goldBorder` (`#0E3A70`) | Ghost button border, focus ring |
| Secondary text | `colorScheme.onSurfaceVariant` (`#8A8A8A`) | Descriptions, metadata |
| Hairline | `colorScheme.outlineVariant` (`#1A2230`) | Card borders, sheet handle |
| READY | `LocalJarvisColors.current.successGreen` | `StatusPill`, `BuildResultCard` success |
| BUILDING | `LocalJarvisColors.current.warningAmber` | `StatusPill`, inline progress |
| GENERATING | `colorScheme.primary` | `StatusPill` |
| FAILED | `colorScheme.error` | `StatusPill`, `BuildResultCard` failure |
| Code surface | `LocalJarvisColors.current.codeBg` (`#0D0D0D`) | `CodeViewer`, `GenerationLogStream` |
| Code border | `LocalJarvisColors.current.codeBorder` (`#2A2A2A`) | `CodeViewer` frame |
| Log text (active line) | `LocalJarvisColors.current.terminalText` (`#C8FFB4`) | Newest line in `GenerationLogStream` |

### 5.2 Typography

| Use | Style |
|---|---|
| TopBar screen title | `titleLarge` (Space Grotesk 22/28 semibold) |
| Section title | `labelMedium` primary-colored (reuse `SectionTitle`) |
| Project card name | `titleMedium` (DM Sans 16/24 semibold) |
| Project card description | `bodySmall` (DM Sans 12/16) |
| Pill / chip label | `labelSmall` (DM Sans 11 medium) |
| Button label | `labelLarge` (DM Sans 14 medium) |
| Card meta ("web · 2h ago") | `LocalJarvisTypography.current.timestamp` |
| Source and logs | `LocalJarvisTypography.current.terminalBody` (JetBrains Mono 13/18) |
| Empty-state title | `titleMedium` |
| Empty-state helper | `bodyMedium` muted |

### 5.3 Spacing — 4dp grid

Private constants in `builder/components/Spacing.kt`:

| Name | Value | Use |
|---|---|---|
| `Space.xs` | 4.dp | Pill internal padding, tight stacks |
| `Space.sm` | 8.dp | Card internal rhythm, chip gaps |
| `Space.md` | 12.dp | Card padding, list vertical spacing |
| `Space.lg` | 16.dp | Screen horizontal padding, section breaks |
| `Space.xl` | 24.dp | Major section breaks, sheet top padding |

### 5.4 Shapes

| Element | Radius |
|---|---|
| Cards | 12.dp |
| Bottom sheet top corners | 20.dp |
| Pills / chips | 999.dp (full round) |
| Segmented control container | 10.dp outer, 7.dp inner active segment |
| Code blocks | 8.dp |
| Primary buttons | 10.dp |

### 5.5 Motion

`READY / IDLE` — static. `GENERATING / BUILDING` — 1.5 Hz opacity pulse on `StatusPill`. Segment transitions use `MotionScheme` default `StandardEasing` at 300 ms. No bespoke curves.

### 5.6 Light mode

Every token above has a light variant in the existing theme (`JarvisLightColorScheme`, `LightJarvisColors`). The screens work in light mode automatically because nothing is hardcoded. A `@Preview(uiMode = UI_MODE_NIGHT_NO)` is added for each new composable to catch regressions.

## 6. State model and ViewModel changes

`AppBuilderViewModel` and its UseCase dependencies stay. The `BuilderUiState` data class gets one new field and one derived value.

### 6.1 Additions to `BuilderUiState`

```kotlin
data class BuilderUiState(
    // ...existing fields unchanged...

    // NEW — which segment the Project screen shows
    val selectedSegment: ProjectSegment = ProjectSegment.DESCRIBE,
) {
    // NEW — single source of truth for StatusPill / BuildResultCard
    val buildPhase: BuildPhase
        get() = when {
            isGenerating                                       -> BuildPhase.GENERATING
            isBuilding                                         -> BuildPhase.BUILDING
            lastBuildResult?.success == true                   -> BuildPhase.READY
            lastBuildResult?.success == false                  -> BuildPhase.FAILED
            selectedProject?.buildStatus == BuildStatus.READY  -> BuildPhase.READY
            else                                               -> BuildPhase.IDLE
        }

    val canRun: Boolean
        get() = buildPhase == BuildPhase.READY && launchPath != null
}

enum class ProjectSegment { DESCRIBE, CODE, RUN }

enum class BuildPhase { IDLE, GENERATING, BUILDING, READY, FAILED }
```

### 6.2 Renames

| Before | After |
|---|---|
| `showNewProjectDialog: Boolean` | `showNewProjectSheet: Boolean` |
| `fun showNewProjectDialog()` / `hideNewProjectDialog()` | `showNewProjectSheet()` / `hideNewProjectSheet()` |

Behavior is identical; the rename matches the new IA (bottom sheet, not dialog).

### 6.3 New VM methods

```kotlin
fun selectSegment(segment: ProjectSegment)
fun selectProjectById(id: String)   // hydrate state for deep-link / direct nav
```

### 6.4 One-shot navigation events

`createNewProject(...)` and `applyTemplate(...)` both end with a navigation to the Project detail screen. A `Channel`-backed flow carries these events so a config change can't replay them.

```kotlin
private val _navEvents = Channel<AppBuilderNav>(capacity = Channel.BUFFERED)
val navEvents: Flow<AppBuilderNav> = _navEvents.receiveAsFlow()

sealed interface AppBuilderNav {
    data class OpenProject(val projectId: String) : AppBuilderNav
}
```

Screens collect:
```kotlin
LaunchedEffect(Unit) {
    vm.navEvents.collect { event ->
        when (event) {
            is AppBuilderNav.OpenProject -> nav.navigate("app_builder/project/${event.projectId}")
        }
    }
}
```

### 6.5 Error surface

`errorMessage: String?` stays. Rendered as an inline banner on the current screen rather than a global `AlertDialog`. Less modal disruption during generate/build.

## 7. Non-goals

1. No change to `AppBuildEngine`, `AppCodeGenerator`, UseCases, Repository, or domain models.
2. No new `AppType` values.
3. No export / share / clone project.
4. No build history beyond `lastBuildResult` — Run pane shows the most recent only.
5. No external syntax-highlighter dependency. Regex-based coloring for HTML/Bash/Python is enough.
6. No live `WebView` preview in the Run pane. Placeholder panel only; WebView wiring is follow-up scope.
7. No collaborative editing, version history, or diff view.

## 8. Risks

| Risk | Mitigation |
|---|---|
| Nav graph change breaks deep links | Keep the old route name `"app_builder"` as the graph root; new destinations nest under it (`app_builder/project/{id}`, `app_builder/templates`). Existing entry points continue to work. |
| Shared VM scoped to the wrong lifecycle loses state on back | Scope to the `app_builder` graph route, not to individual destinations. Smoke test: Home → Project → back → Home preserves `selectedSegment` and the list. |
| Monolithic `BuilderUiState` — three screens read one blob | Accept for now. Splitting into three VMs doubles repository wiring and introduces cross-VM sync. Revisit when Home starts needing state the Project screen shouldn't see. |
| Segment state lost across config change | `rememberSaveable` for the segment index in `AppBuilderProjectScreen`, seeded from `BuilderUiState.selectedSegment`. |
| Focus lost when `ModalBottomSheet` opens with keyboard | `skipPartiallyExpanded = true`; focus the name field inside a `LaunchedEffect(Unit)` after the sheet state goes expanded. |
| `CodeViewer` jitters on large generated files | `LazyColumn` with line-keyed items; regex tokenization clipped to the visible range via `derivedStateOf`. |
| Light mode never actually tested | Add `@Preview(uiMode = UI_MODE_NIGHT_NO)` for every new composable. A failing preview signals a hardcoded color. |
| Old `AppBuilderScreen.kt` still referenced from nav graph / elsewhere | Grep for it before deletion. Likely only the nav entry. Point it at `AppBuilderHomeScreen` and delete the old file. |

## 9. Testing

### 9.1 ViewModel unit tests (JUnit + Turbine on `StateFlow`)

- `buildPhase` transitions idle → generating → idle-with-code → building → ready / failed, matching VM emissions.
- `canRun` flips exactly when `buildPhase == READY && launchPath != null`.
- `selectSegment` is a pure setter — emits one state change per unique value, zero for the same value.
- `navEvents` emits exactly one `OpenProject` per `createNewProject` success, and zero on failure paths.
- `applyTemplate` pre-fills description only when blank (existing behavior preserved).

### 9.2 Composable screenshot tests

Whatever screenshot-testing harness the project already has (Paparazzi or Roborazzi — to be checked at plan time). Subjects:

- `ProjectCard` in each of five `BuildPhase` states.
- `StatusPill` in each phase.
- Empty states: no projects, no source code yet, not built yet.
- Light and dark render for every subject.

### 9.3 Manual smoke

- Home → `+` → sheet → Create → Project/Describe. Title shows "Untitled app". Description empty. Type = Web.
- Project/Describe → fill description → Generate → log streams → Code tab has content → Build → Run.
- Long-generated code (≈1000 lines) scrolls in `CodeViewer` without jank.
- Background the app mid-generation; foreground restores the generating state correctly.
- Rotate on every screen; no state loss.
- Light-mode smoke on Home and Project screens.

### 9.4 What is not tested

UseCases, Repository, `AppBuildEngine`, `AppCodeGenerator` — unchanged, covered by their own existing tests.

## 10. Deliverables

1. Three new screen files: `AppBuilderHomeScreen.kt`, `AppBuilderProjectScreen.kt`, `AppBuilderTemplatesScreen.kt`.
2. One new dialog file or a new `CodeEditorScreen.kt` for full-screen edit.
3. New `components/` package with the atoms listed in §4.1.
4. Updated `AppBuilderViewModel.kt` with `selectedSegment`, `buildPhase`, navigation channel, renames.
5. Updated nav graph: new routes under `app_builder`, old `AppBuilderScreen.kt` deleted.
6. Deleted: `AppBuilderScreen.kt` (original composite), `BuildStatusBadge`, `AppTypeChip`, local `jarvisTextFieldColors()`.
7. Unit and screenshot tests per §9.
