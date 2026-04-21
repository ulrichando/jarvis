package com.jarvis.android.presentation.chat

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Build
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import com.jarvis.android.domain.model.Message
import com.jarvis.android.domain.model.MessageContentType
import com.jarvis.android.domain.model.MessageRole
import com.jarvis.android.presentation.components.ConversationDrawer
import com.jarvis.android.presentation.components.JarvisInputBar
import com.jarvis.android.presentation.components.MessageBubble
import com.jarvis.android.presentation.components.StreamingCursor
import kotlinx.coroutines.launch

// ── Design tokens — single source of truth, used nowhere else ─────────────────
//
// These are the only colours the screen defines; everything else comes from
// [JarvisPalette] / the Material theme so dark-mode tweaks land globally.

private val ScreenBg    = Color(0xFF0A0A0A)
private val Accent      = Color(0xFF1E7FFF)
private val TextPrimary = Color(0xFFECECEC)
private val TextMuted   = Color(0xFF8A8A8A)

/**
 * Root screen of the JARVIS Android experience — Home + chat + voice, all in one.
 *
 * ### Layout contract
 *
 * On **empty state** (no messages yet):
 * ```
 *   [☰]        JARVIS         [⋮]
 *
 *         [ArcReactor 200dp]
 *
 *      Good evening, JARVIS is here!
 *        What can I help you with?
 *
 *     [🌐 Scan]    [⚡ System]
 *     [💻 Script]  [📊 Logs]
 *     [🔍 Research] [✨ Surprise]
 *
 *   [ type… ]             [🎤] [⬆]
 * ```
 *
 * On **conversation state** (messages exist):
 * ```
 *   [☰]    JARVIS • live         [⋮]   ← reactor collapses to a chip
 *
 *   ── message bubbles ──
 *
 *   [ type… ]             [🎤] [⬆]
 * ```
 *
 * Pressing the mic opens a full-screen [VoiceOverlay]; the reactor continues
 * animating there at full size. Tool screens are reached via the overflow menu
 * on the top-right — the drawer is reserved for conversation history.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(
    onNavigateToTerminal:    () -> Unit = {},
    onNavigateToFiles:       () -> Unit = {},
    onNavigateToSystem:      () -> Unit = {},
    onNavigateToNetwork:     () -> Unit = {},
    onNavigateToSensors:     () -> Unit = {},
    onNavigateToPermissions: () -> Unit = {},
    onNavigateToSettings:    () -> Unit = {},
    onNavigateToLocalAi:     () -> Unit = {},
    onNavigateToAppBuilder:  () -> Unit = {},
    onNavigateToCyberSuite:  () -> Unit = {},
    viewModel:               ChatViewModel = hiltViewModel(),
) {
    val uiState       by viewModel.uiState.collectAsState()
    val drawerState   = rememberDrawerStateWithSnapShot()
    val scope         = rememberCoroutineScope()
    val snackbarState = remember { SnackbarHostState() }
    val listState     = rememberLazyListState()
    val ctx           = LocalContext.current

    // Runtime mic permission — shown once at mount so voice never silently fails.
    val micGranted = rememberMicPermissionOnce()

    // Real-time mic amplitude, drives the reactor / voice-bar pulsing. The
    // monitor is cheap enough to run for the whole session — ~10 ms buffer
    // reads + a normalised float flow.
    val micMonitor = remember { MicAmplitudeMonitor(ctx) }
    val micLevel   by micMonitor.level.collectAsState()

    DisposableEffect(micGranted) {
        if (micGranted) micMonitor.start(scope)
        onDispose { micMonitor.stop() }
    }

    // Voice overlay visibility — starts recording when shown.
    var voiceOverlayVisible by remember { mutableStateOf(false) }

    // Long-press delete confirmation
    var deleteConvId by remember { mutableStateOf<String?>(null) }
    var deleteConvTitle by remember { mutableStateOf("") }

    // Surface errors as a Snackbar
    LaunchedEffect(uiState.error) {
        uiState.error?.let {
            snackbarState.showSnackbar(it)
            viewModel.onIntent(ChatIntent.ClearError)
        }
    }

    // Autoscroll to newest message / streaming bubble
    val messageCount = uiState.messages.size
    val streamLen    = uiState.streamingText.length
    LaunchedEffect(messageCount, streamLen > 0) {
        val total = messageCount + if (uiState.streamingText.isNotEmpty()) 1 else 0
        if (total > 0) listState.animateScrollToItem(total - 1)
    }

    // Close the voice overlay when recording stops AND we have a final transcript
    // in inputText — then auto-send, so "tap mic → speak → done" becomes one flow.
    LaunchedEffect(uiState.isRecording, voiceOverlayVisible) {
        if (voiceOverlayVisible && !uiState.isRecording && uiState.inputText.isNotBlank()) {
            voiceOverlayVisible = false
            viewModel.onIntent(ChatIntent.SendMessage())
        }
    }

    ModalNavigationDrawer(
        drawerState   = drawerState,
        drawerContent = {
            ConversationDrawer(
                conversations     = uiState.conversations,
                activeId          = uiState.activeConversationId,
                onSelect          = { conv ->
                    viewModel.onIntent(ChatIntent.SelectConversation(conv.id))
                    scope.launch { drawerState.close() }
                },
                onNewConversation = {
                    viewModel.onIntent(ChatIntent.NewConversation)
                    scope.launch { drawerState.close() }
                },
                onLongClick       = { conv ->
                    deleteConvId    = conv.id
                    deleteConvTitle = conv.title
                },
            )
        },
    ) {
        Box(modifier = Modifier.fillMaxSize().background(ScreenBg)) {
            Scaffold(
                containerColor = ScreenBg,
                snackbarHost   = { SnackbarHost(snackbarState) },
                topBar = {
                    HomeTopBar(
                        isLive                = uiState.isStreaming || uiState.isTtsSpeaking,
                        routingLabel          = uiState.routingLabel,
                        downloadedModels      = uiState.downloadedModels,
                        loadedLocalModelId    = uiState.loadedLocalModelId,
                        loadingLocalModelId   = uiState.loadingLocalModelId,
                        availableCloudModels  = uiState.availableCloudModels,
                        selectedCloudModelId  = uiState.selectedCloudModelId,
                        onMenu                = { scope.launch { drawerState.open() } },
                        onSelectCloudModel    = { id -> viewModel.onIntent(ChatIntent.SelectCloudModel(id)) },
                        onSelectLocalModel    = { id -> viewModel.onIntent(ChatIntent.SelectLocalModel(id)) },
                        onOpenLocalAi         = onNavigateToLocalAi,
                        onOpenSettings        = onNavigateToSettings,
                        onTerminal            = onNavigateToTerminal,
                        onFiles               = onNavigateToFiles,
                        onSystem              = onNavigateToSystem,
                        onNetwork             = onNavigateToNetwork,
                        onSensors             = onNavigateToSensors,
                        onPermissions         = onNavigateToPermissions,
                        onSettings            = onNavigateToSettings,
                        onLocalAi             = onNavigateToLocalAi,
                        onAppBuilder          = onNavigateToAppBuilder,
                        onCyberSuite          = onNavigateToCyberSuite,
                    )
                },
                bottomBar = {
                    JarvisInputBar(
                        text           = uiState.inputText,
                        onTextChange   = { viewModel.onIntent(ChatIntent.UpdateInput(it)) },
                        onSend         = { viewModel.onIntent(ChatIntent.SendMessage()) },
                        onStop         = { viewModel.onIntent(ChatIntent.StopStreaming) },
                        isStreaming    = uiState.isStreaming,
                        enabled        = true,
                        // Quick dictation — text field fills with the transcript.
                        onVoice        = { viewModel.onIntent(ChatIntent.ToggleVoice) },
                        // Full-screen voice mode — opens the reactor overlay and
                        // starts recording in one gesture.
                        onVoiceMode    = {
                            voiceOverlayVisible = true
                            if (!uiState.isRecording) {
                                viewModel.onIntent(ChatIntent.ToggleVoice)
                            }
                        },
                        isRecording    = uiState.isRecording,
                        modifier       = Modifier.imePadding(),
                    )
                },
            ) { padding ->
                HomeBody(
                    uiState    = uiState,
                    listState  = listState,
                    padding    = padding,
                    micLevel   = micLevel,
                    onSuggestedPrompt = { prompt ->
                        viewModel.onIntent(ChatIntent.UpdateInput(prompt.prompt))
                        viewModel.onIntent(ChatIntent.SendMessage())
                    },
                )
            }

            // ── Voice overlay — sits above everything when active ────────────
            //
            // Driven by local [voiceOverlayVisible] so the overlay can be
            // dismissed without cancelling the mic (and vice versa).
            VoiceOverlay(
                isVisible   = voiceOverlayVisible,
                isRecording = uiState.isRecording,
                audioLevel  = micLevel,
                transcript  = uiState.inputText,
                onToggleMic = { viewModel.onIntent(ChatIntent.ToggleVoice) },
                onDismiss   = {
                    if (uiState.isRecording) viewModel.onIntent(ChatIntent.ToggleVoice)
                    voiceOverlayVisible = false
                },
            )
        }
    }

    // ── Delete conversation confirmation ─────────────────────────────────────
    deleteConvId?.let { convId ->
        AlertDialog(
            onDismissRequest = { deleteConvId = null },
            containerColor   = Color(0xFF141414),
            title = { Text("Delete conversation?", color = TextPrimary) },
            text  = { Text("\"$deleteConvTitle\" will be permanently deleted.",
                           color = TextMuted) },
            confirmButton = {
                TextButton(onClick = {
                    viewModel.onIntent(ChatIntent.DeleteConversation(convId))
                    deleteConvId = null
                }) { Text("Delete", color = Color(0xFFCF4A3C)) }
            },
            dismissButton = {
                TextButton(onClick = { deleteConvId = null }) {
                    Text("Cancel", color = TextMuted)
                }
            },
        )
    }

    // ── Tool-confirmation dialog (agentic tool permissions) ───────────────────
    uiState.pendingConfirmation?.let { req ->
        AlertDialog(
            onDismissRequest = {
                viewModel.onIntent(ChatIntent.ResolveConfirmation(req.id, allowed = false))
            },
            containerColor = Color(0xFF141414),
            title = { Text("Confirm: ${req.description}", color = TextPrimary) },
            text  = {
                Column {
                    Text(req.description, color = TextPrimary)
                    Spacer(Modifier.height(8.dp))
                    Text(req.detail, style = MaterialTheme.typography.bodySmall, color = TextMuted)
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    viewModel.onIntent(ChatIntent.ResolveConfirmation(req.id, allowed = true))
                }) { Text("Allow", color = Accent) }
            },
            dismissButton = {
                TextButton(onClick = {
                    viewModel.onIntent(ChatIntent.ResolveConfirmation(req.id, allowed = false))
                }) { Text("Deny", color = TextMuted) }
            },
        )
    }
}

// ── Home body: reactor hero vs message list ───────────────────────────────────

/**
 * Main content area. Switches between hero (empty state) and message list.
 * The reactor shrinks from 200dp to 56dp when a conversation exists, so
 * it remains "present" but yields space to the messages.
 */
@Composable
private fun HomeBody(
    uiState:          ChatUiState,
    listState:        androidx.compose.foundation.lazy.LazyListState,
    padding:          PaddingValues,
    micLevel:         Float,
    onSuggestedPrompt: (SuggestedPrompt) -> Unit,
) {
    // With the new minimal empty-state design, the reactor is not on Home any
    // more — it only appears in the [VoiceOverlay]. A small presence indicator
    // in the top bar handles "the AI is live" moments.
    val aiActive = uiState.isStreaming || uiState.isTtsSpeaking
    @Suppress("UNUSED_PARAMETER") val _reactorAudio = micLevel // retained for future

    Box(
        modifier = Modifier
            .fillMaxSize()
            .padding(padding),
    ) {
        if (uiState.hasContent) {
            MessageList(
                messages        = uiState.messages,
                streamingText   = uiState.streamingText,
                activeToolCalls = uiState.activeToolCalls,
                isStreaming     = uiState.isStreaming,
                listState       = listState,
                modifier        = Modifier.fillMaxSize(),
            )
        } else {
            HomeHero(
                onPromptClick = onSuggestedPrompt,
                modifier      = Modifier.fillMaxSize(),
            )
        }
    }

    // Silence unused-variable warning for aiActive while the live-chip lives
    // in the top bar and doesn't need a body-level signal.
    @Suppress("UNUSED_EXPRESSION") aiActive
}

// ── Top bar ───────────────────────────────────────────────────────────────────

/**
 * Top bar — Claude-style: hamburger left, centered tappable brand+routing
 * label (doubles as the model selector), tool overflow on the right.
 *
 * ```
 *   [☰]        JARVIS · Auto ▾        [⋮]
 * ```
 *
 * The centered label is the routing-mode switcher. Tapping it cycles through
 * Auto / Local / Cloud / Hybrid (or whatever the ViewModel exposes next),
 * matching the behaviour the old "Auto ▾" chip in the input bar used to have.
 */
@Composable
private fun HomeTopBar(
    isLive:                Boolean,
    routingLabel:          String,
    downloadedModels:      List<com.jarvis.android.domain.model.ModelEntry>,
    loadedLocalModelId:    String?,
    loadingLocalModelId:   String?,
    availableCloudModels:  List<com.jarvis.android.domain.model.CloudModel>,
    selectedCloudModelId:  String?,
    onMenu:                () -> Unit,
    onSelectCloudModel:    (String) -> Unit,
    onSelectLocalModel:    (String) -> Unit,
    onOpenLocalAi:         () -> Unit,
    onOpenSettings:        () -> Unit,
    onTerminal:          () -> Unit,
    onFiles:             () -> Unit,
    onSystem:            () -> Unit,
    onNetwork:           () -> Unit,
    onSensors:           () -> Unit,
    onPermissions:       () -> Unit,
    onSettings:          () -> Unit,
    onLocalAi:           () -> Unit,
    onAppBuilder:        () -> Unit,
    onCyberSuite:        () -> Unit,
) {
    var toolMenuOpen  by remember { mutableStateOf(false) }
    var modelMenuOpen by remember { mutableStateOf(false) }

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .statusBarsPadding()
            // Samsung S24/S26 Ultra's front-camera punch-hole sits in the
            // centre of the status bar. statusBarsPadding() stops exactly at
            // the bottom of the status bar, which leaves the title colliding
            // with the camera cutout. Push the bar another 14dp down so the
            // JARVIS label clears the lens on full-screen displays.
            .padding(start = 4.dp, end = 4.dp, top = 14.dp, bottom = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        IconButton(onClick = onMenu) {
            Icon(
                imageVector        = Icons.Default.Menu,
                contentDescription = "Conversations",
                tint               = TextPrimary.copy(alpha = 0.9f),
            )
        }

        // ── Center: model / backend picker ────────────────────────────────
        //
        // The JARVIS brand and the model selector are separated so the
        // dropdown anchors to the model-label region — it drops directly
        // under "<routing label> ▾" instead of hanging off the left of the
        // whole title. This matches the Claude-mobile behaviour.
        Box(modifier = Modifier.weight(1f), contentAlignment = Alignment.Center) {
            Row(
                verticalAlignment     = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.Center,
            ) {
                Text(
                    text  = "JARVIS",
                    style = MaterialTheme.typography.titleMedium.copy(
                        fontWeight    = FontWeight.Bold,
                        fontSize      = 16.sp,
                        letterSpacing = 0.3.sp,
                    ),
                    color = TextPrimary,
                )
                Spacer(Modifier.width(6.dp))

                // This inner Box is the dropdown anchor — the menu opens
                // right below it so its top-left lines up with the tapped
                // "<label> ▾" region rather than the left edge of the bar.
                Box {
                    Row(
                        modifier              = Modifier.clickable { modelMenuOpen = true },
                        verticalAlignment     = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.Center,
                    ) {
                        Text(
                            text  = routingLabel,
                            style = MaterialTheme.typography.titleMedium.copy(
                                fontWeight = FontWeight.Normal,
                                fontSize   = 15.sp,
                            ),
                            color = TextMuted,
                        )
                        Spacer(Modifier.width(2.dp))
                        Icon(
                            imageVector        = Icons.Default.KeyboardArrowDown,
                            contentDescription = "Pick a model",
                            tint               = TextMuted,
                            modifier           = Modifier.size(18.dp),
                        )
                    }

                    DropdownMenu(
                        expanded         = modelMenuOpen,
                        onDismissRequest = { modelMenuOpen = false },
                        modifier         = Modifier.background(Color(0xFF1C1C1E)),
                    ) {
                val nothingAvailable = availableCloudModels.isEmpty() && downloadedModels.isEmpty()

                if (nothingAvailable) {
                    // Neither an API key nor a local model — point the user
                    // at the two places they can unlock a backend from.
                    DropdownMenuItem(
                        text = {
                            Column {
                                Text(
                                    text  = "No models available",
                                    color = TextPrimary,
                                    fontSize = 13.sp,
                                )
                                Text(
                                    text  = "Add an API key in Settings, or download a model.",
                                    color = TextMuted,
                                    fontSize = 11.sp,
                                )
                            }
                        },
                        onClick = {
                            modelMenuOpen = false
                            onOpenSettings()
                        },
                    )
                    DropdownMenuItem(
                        text = { Text("Download a local model", color = Accent, fontSize = 12.sp) },
                        onClick = {
                            modelMenuOpen = false
                            onOpenLocalAi()
                        },
                    )
                }

                // ── API models (grouped by provider, gated on key) ───────
                if (availableCloudModels.isNotEmpty()) {
                    DropdownSectionLabel("CLOUD")
                    // Group by provider so the list reads as "Anthropic / ...,
                    // DeepSeek / ..., Groq / ..." instead of a flat mix.
                    availableCloudModels.groupBy { it.provider }.forEach { (provider, models) ->
                        models.forEach { m ->
                            val isSelected = m.id == selectedCloudModelId
                            DropdownMenuItem(
                                text = {
                                    Row(verticalAlignment = Alignment.CenterVertically) {
                                        Column(modifier = Modifier.weight(1f)) {
                                            Text(
                                                text  = m.label,
                                                color = if (isSelected) Accent else TextPrimary,
                                                fontWeight = if (isSelected) FontWeight.Medium else FontWeight.Normal,
                                                fontSize = 13.sp,
                                            )
                                            Text(
                                                text  = provider.displayName,
                                                color = TextMuted,
                                                fontSize = 10.sp,
                                            )
                                        }
                                        if (isSelected) {
                                            Icon(
                                                imageVector        = Icons.Default.Check,
                                                contentDescription = "Active",
                                                tint               = Accent,
                                                modifier           = Modifier.size(14.dp),
                                            )
                                        }
                                    }
                                },
                                onClick = {
                                    modelMenuOpen = false
                                    onSelectCloudModel(m.id)
                                },
                            )
                        }
                    }
                }

                // ── On-device models (downloaded locally) ────────────────
                if (downloadedModels.isNotEmpty()) {
                    DropdownSectionLabel("ON DEVICE")
                    downloadedModels.forEach { model ->
                        val isSelected = model.id == loadedLocalModelId
                        val isLoading  = model.id == loadingLocalModelId
                        DropdownMenuItem(
                            text = {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Text(
                                        text  = model.name,
                                        color = if (isSelected) Accent else TextPrimary,
                                        fontWeight = if (isSelected) FontWeight.Medium else FontWeight.Normal,
                                        fontSize = 13.sp,
                                        modifier = Modifier.weight(1f),
                                    )
                                    Spacer(Modifier.width(8.dp))
                                    if (isLoading) {
                                        androidx.compose.material3.CircularProgressIndicator(
                                            modifier    = Modifier.size(14.dp),
                                            strokeWidth = 2.dp,
                                            color       = Accent,
                                        )
                                    } else if (isSelected) {
                                        Icon(
                                            imageVector        = Icons.Default.Check,
                                            contentDescription = "Loaded",
                                            tint               = Accent,
                                            modifier           = Modifier.size(14.dp),
                                        )
                                    }
                                }
                            },
                            onClick = {
                                modelMenuOpen = false
                                onSelectLocalModel(model.id)
                            },
                        )
                    }
                }
                    }  // close DropdownMenu
                }      // close anchor Box
                if (isLive) {
                    Spacer(Modifier.width(6.dp))
                    LiveChip()
                }
            }          // close outer Row
        }              // close weight(1f) Box

        Box {
            IconButton(onClick = { toolMenuOpen = true }) {
                Icon(
                    imageVector        = Icons.Default.MoreVert,
                    contentDescription = "Tools",
                    tint               = TextPrimary.copy(alpha = 0.9f),
                )
            }
            DropdownMenu(
                expanded         = toolMenuOpen,
                onDismissRequest = { toolMenuOpen = false },
                modifier         = Modifier.background(Color(0xFF1C1C1E)),
            ) {
                ToolMenuItem("Terminal",     onTerminal)    { toolMenuOpen = false }
                ToolMenuItem("File Manager", onFiles)       { toolMenuOpen = false }
                ToolMenuItem("System",       onSystem)      { toolMenuOpen = false }
                ToolMenuItem("Network",      onNetwork)     { toolMenuOpen = false }
                ToolMenuItem("Sensors",      onSensors)     { toolMenuOpen = false }
                ToolMenuItem("Permissions",  onPermissions) { toolMenuOpen = false }
                ToolMenuItem("Settings",     onSettings)    { toolMenuOpen = false }
                ToolMenuItem("Local AI",     onLocalAi)     { toolMenuOpen = false }
                ToolMenuItem("App Builder",  onAppBuilder)  { toolMenuOpen = false }
                ToolMenuItem("Cyber Suite",  onCyberSuite)  { toolMenuOpen = false }
            }
        }
    }
}

@Composable
private fun ToolMenuItem(label: String, onNavigate: () -> Unit, onClose: () -> Unit) {
    DropdownMenuItem(
        text    = { Text(label, color = TextPrimary) },
        onClick = { onClose(); onNavigate() },
    )
}

/** Small uppercase section header inside the home-bar model dropdown. */
@Composable
private fun DropdownSectionLabel(text: String) {
    Text(
        text     = text,
        color    = TextMuted,
        fontSize = 10.sp,
        fontWeight = FontWeight.SemiBold,
        modifier = Modifier.padding(start = 16.dp, end = 16.dp, top = 10.dp, bottom = 4.dp),
    )
}

/** Tiny animated chip showing "live" status when the AI is speaking/streaming. */
@Composable
private fun LiveChip() {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier
            .background(
                color = Accent.copy(alpha = 0.14f),
                shape = RoundedCornerShape(999.dp),
            )
            .border(
                width = 1.dp,
                color = Accent.copy(alpha = 0.35f),
                shape = RoundedCornerShape(999.dp),
            )
            .padding(horizontal = 8.dp, vertical = 2.dp),
    ) {
        Box(
            modifier = Modifier
                .size(6.dp)
                .background(Accent, CircleShape),
        )
        Spacer(Modifier.width(5.dp))
        Text(
            text  = "live",
            style = MaterialTheme.typography.labelSmall.copy(fontSize = 11.sp),
            color = Accent,
        )
    }
}

// ── Message list ──────────────────────────────────────────────────────────────

@Composable
private fun MessageList(
    messages:        List<Message>,
    streamingText:   String,
    activeToolCalls: List<ActiveToolCall>,
    isStreaming:     Boolean,
    listState:       androidx.compose.foundation.lazy.LazyListState,
    modifier:        Modifier = Modifier,
) {
    LazyColumn(
        modifier            = modifier,
        state               = listState,
        contentPadding      = PaddingValues(horizontal = 8.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        items(items = messages, key = { it.id }) { msg ->
            MessageBubble(message = msg)
        }

        if (activeToolCalls.isNotEmpty()) {
            item(key = "tool_strip") {
                ToolCallStrip(toolCalls = activeToolCalls, modifier = Modifier.padding(horizontal = 8.dp))
            }
        }

        if (isStreaming && streamingText.isNotEmpty()) {
            item(key = "streaming") { StreamingBubble(streamingText) }
        } else if (isStreaming) {
            item(key = "thinking") {
                ThinkingIndicator(modifier = Modifier.padding(horizontal = 12.dp, vertical = 4.dp))
            }
        }
    }
}

@Composable
private fun StreamingBubble(text: String) {
    val ghost = remember(text) {
        Message(
            id = -1L, conversationId = "", role = MessageRole.ASSISTANT,
            content = text, contentType = MessageContentType.TEXT,
            toolCallsJson = null, timestamp = System.currentTimeMillis(),
            inputTokens = 0, outputTokens = 0, stopReason = null, isOffline = false,
        )
    }
    MessageBubble(message = ghost, streamingText = text, isStreaming = true)
}

@Composable
private fun ToolCallStrip(toolCalls: List<ActiveToolCall>, modifier: Modifier = Modifier) {
    Column(modifier = modifier) {
        toolCalls.forEach { tc ->
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier          = Modifier.padding(vertical = 2.dp),
            ) {
                Icon(
                    imageVector        = Icons.Default.Build,
                    contentDescription = null,
                    tint               = if (tc.isError) MaterialTheme.colorScheme.error else Accent,
                    modifier           = Modifier.size(14.dp),
                )
                Spacer(Modifier.width(6.dp))
                Text(
                    text  = if (tc.isCompleted) "✓ ${tc.name}" else "⟳ ${tc.name}",
                    style = MaterialTheme.typography.labelSmall,
                    color = if (tc.isError) MaterialTheme.colorScheme.error else TextMuted,
                )
            }
        }
    }
}

@Composable
private fun ThinkingIndicator(modifier: Modifier = Modifier) {
    Row(modifier = modifier, verticalAlignment = Alignment.CenterVertically) {
        Text(text = "Thinking", style = MaterialTheme.typography.bodySmall, color = TextMuted)
        Spacer(Modifier.width(4.dp))
        StreamingCursor()
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Wraps [rememberDrawerState] with a stable initial value. Extracted to keep
 * the main Composable readable.
 */
@Composable
private fun rememberDrawerStateWithSnapShot() =
    androidx.compose.material3.rememberDrawerState(initialValue = DrawerValue.Closed)

/**
 * Requests the RECORD_AUDIO permission once on first composition and returns
 * the latest grant state. Placed on the root screen so the user is never faced
 * with a dead mic button, and so downstream consumers (the mic monitor) can
 * react when the permission is granted.
 */
@Composable
private fun rememberMicPermissionOnce(): Boolean {
    val ctx = LocalContext.current
    var granted by remember {
        mutableStateOf(
            ctx.checkSelfPermission(Manifest.permission.RECORD_AUDIO) ==
                PackageManager.PERMISSION_GRANTED,
        )
    }
    val launcher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.RequestPermission(),
    ) { result -> granted = result }
    LaunchedEffect(Unit) {
        if (!granted) launcher.launch(Manifest.permission.RECORD_AUDIO)
    }
    return granted
}
