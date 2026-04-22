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
    onNavigateToChats:       () -> Unit = {},
    initialConversationId:   String?    = null,
    viewModel:               ChatViewModel = hiltViewModel(),
) {
    val uiState       by viewModel.uiState.collectAsState()
    val drawerState   = rememberDrawerStateWithSnapShot()
    val scope         = rememberCoroutineScope()
    val snackbarState = remember { SnackbarHostState() }
    val listState     = rememberLazyListState()
    val ctx           = LocalContext.current

    // Mic permission state — see the on-demand launcher set up below the
    // voiceOverlayVisible declaration. Defaults to whatever Android currently
    // grants (no prompt at app launch).
    var micGranted by remember {
        mutableStateOf(
            ctx.checkSelfPermission(Manifest.permission.RECORD_AUDIO) ==
                android.content.pm.PackageManager.PERMISSION_GRANTED,
        )
    }

    // No standalone amplitude monitor — it would compete with the
    // SpeechRecognizer for the microphone and cause STT to silently capture
    // zero audio (NO_SPEECH_DETECTED). The voice overlay no longer renders an
    // amplitude visualization, so micLevel is permanently zero.
    val micLevel = 0f

    // Voice overlay visibility — starts recording when shown.
    var voiceOverlayVisible by remember { mutableStateOf(false) }

    // Explicit user-mute toggle for the voice-mode mic. Independent of the
    // STT engine's recording state, which naturally pauses while the AI is
    // speaking. Only the user tapping the mic mute button flips this; the
    // auto-restart loop respects it (stays off while muted).
    var userMutedMic by remember { mutableStateOf(false) }

    // (MicAmplitudeMonitor lifecycle removed — it stole the mic from
    // SpeechRecognizer and broke STT. STT owns the mic exclusively now.)

    // Mic + TTS shutdown on EVERY exit path. The Stop button does this in
    // its onClick, but back-press / swipe-up / app destroy never trigger
    // that handler. This DisposableEffect runs on Composition leave AND
    // when voiceOverlayVisible flips false, releasing the microphone so
    // Android's green mic indicator goes away the instant the user leaves.
    DisposableEffect(voiceOverlayVisible) {
        onDispose {
            // Stop both ends regardless — extra calls to a stopped engine
            // are no-ops, and missing one means a stuck mic icon.
            if (uiState.isRecording) viewModel.onIntent(ChatIntent.ToggleVoice)
            viewModel.onIntent(ChatIntent.SetTtsEnabled(false))
        }
    }

    // On-demand mic permission flow. Tapping mic / voice-mode sets a pending
    // action, then either (a) fires it immediately if already granted, or
    // (b) launches the system permission prompt and fires it on success.
    var pendingMicAction by remember { mutableStateOf<MicAction?>(null) }
    val micPermLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.RequestPermission(),
    ) { granted ->
        micGranted = granted
        if (granted) when (pendingMicAction) {
            MicAction.Dictate   -> viewModel.onIntent(ChatIntent.ToggleVoice)
            MicAction.VoiceMode -> {
                voiceOverlayVisible = true
                viewModel.onIntent(ChatIntent.SetTtsEnabled(true))
                viewModel.onIntent(ChatIntent.ToggleVoice)
            }
            null -> Unit
        }
        pendingMicAction = null
    }
    val requestMicThen: (MicAction) -> Unit = { action ->
        if (micGranted) {
            // Permission already granted — fire immediately.
            when (action) {
                MicAction.Dictate   -> viewModel.onIntent(ChatIntent.ToggleVoice)
                MicAction.VoiceMode -> {
                    voiceOverlayVisible = true
                    viewModel.onIntent(ChatIntent.SetTtsEnabled(true))
                    viewModel.onIntent(ChatIntent.ToggleVoice)
                }
            }
        } else {
            // Otherwise stash intent + ask Android. Result handled above.
            pendingMicAction = action
            micPermLauncher.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    // Long-press delete confirmation (also reused by the top-bar overflow Delete)
    var deleteConvId by remember { mutableStateOf<String?>(null) }
    var deleteConvTitle by remember { mutableStateOf("") }
    // Top-bar overflow Rename — opens a small text-field dialog
    var renameConvId by remember { mutableStateOf<String?>(null) }
    var renameDraft  by remember { mutableStateOf("") }

    // If we got here from the Chats list with a specific conversation id,
    // tell the VM to switch to it. Guard with the "default" sentinel and a
    // recompose-stable key so we don't fire on every recomposition.
    LaunchedEffect(initialConversationId) {
        if (!initialConversationId.isNullOrBlank() && initialConversationId != "default") {
            viewModel.onIntent(ChatIntent.SelectConversation(initialConversationId))
        }
    }

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

    // Voice-mode pipeline:
    //   1. STT finalises  →  auto-send the transcript (don't close the overlay)
    //   2. Assistant streams + TTS speaks
    //   3. When BOTH finish  →  re-arm the mic so the conversation continues
    //      hands-free without the user touching anything.
    LaunchedEffect(uiState.isRecording, voiceOverlayVisible) {
        if (voiceOverlayVisible && !uiState.isRecording && uiState.inputText.isNotBlank()) {
            viewModel.onIntent(ChatIntent.SendMessage())
        }
    }
    LaunchedEffect(uiState.isStreaming, uiState.isTtsSpeaking, voiceOverlayVisible, userMutedMic) {
        // Both the model's stream AND the spoken playback need to be done
        // before we re-open the mic, otherwise we'd record our own TTS.
        // userMutedMic gates the auto-restart so an explicit mute stays muted.
        if (voiceOverlayVisible &&
            !userMutedMic &&
            !uiState.isStreaming &&
            !uiState.isTtsSpeaking &&
            !uiState.isRecording &&
            uiState.inputText.isBlank()
        ) {
            viewModel.onIntent(ChatIntent.ToggleVoice)
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
                onOpenSettings    = {
                    scope.launch { drawerState.close() }
                    onNavigateToSettings()
                },
                onOpenChats       = {
                    scope.launch { drawerState.close() }
                    onNavigateToChats()
                },
                onOpenLocalAi     = {
                    scope.launch { drawerState.close() }
                    onNavigateToLocalAi()
                },
                onOpenFiles       = {
                    scope.launch { drawerState.close() }
                    onNavigateToFiles()
                },
                onOpenTerminal    = {
                    scope.launch { drawerState.close() }
                    onNavigateToTerminal()
                },
                onOpenAppBuilder  = {
                    scope.launch { drawerState.close() }
                    onNavigateToAppBuilder()
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
                        activeConversation    = uiState.conversations
                            .firstOrNull { it.id == uiState.activeConversationId },
                        onRename = {
                            val active = uiState.conversations
                                .firstOrNull { it.id == uiState.activeConversationId }
                            if (active != null) {
                                renameConvId = active.id
                                renameDraft  = active.title
                            }
                        },
                        onTogglePin = {
                            val active = uiState.conversations
                                .firstOrNull { it.id == uiState.activeConversationId }
                            if (active != null) {
                                viewModel.onIntent(
                                    ChatIntent.PinConversation(active.id, !active.isPinned),
                                )
                            }
                        },
                        onDelete = {
                            val active = uiState.conversations
                                .firstOrNull { it.id == uiState.activeConversationId }
                            if (active != null) {
                                deleteConvId    = active.id
                                deleteConvTitle = active.title
                            }
                        },
                        onNewChat = { viewModel.onIntent(ChatIntent.NewConversation) },
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
                        // requestMicThen pops the system permission prompt the
                        // first time and fires the action the moment it's granted.
                        onVoice        = { requestMicThen(MicAction.Dictate) },
                        // Full-screen voice mode — opens the reactor overlay
                        // and starts recording in one gesture (also flips TTS
                        // on while the overlay is visible).
                        onVoiceMode    = { requestMicThen(MicAction.VoiceMode) },
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
            // Claude-style: scrollable transcript in the middle, gear / mute /
            // Stop pill at the bottom. Mute toggles the user's mic without
            // leaving voice mode; Stop cancels recording, kills TTS, and
            // dismisses the overlay back to text chat.
            VoiceOverlay(
                isVisible      = voiceOverlayVisible,
                isRecording    = uiState.isRecording,
                audioLevel     = micLevel,
                messages       = uiState.messages,
                streamingText  = uiState.streamingText,
                liveTranscript = uiState.inputText,
                isMuted        = userMutedMic,
                // Drives the bottom glow — only when the model is actually
                // producing output. `isStreaming` flips true the moment
                // SendMessage fires (before any API round-trip), which used
                // to make the glow show during the "waiting" phase. Now we
                // gate on actual content: tokens arriving (streamingText
                // non-blank) or TTS audio playing.
                isAiSpeaking   = uiState.streamingText.isNotBlank() || uiState.isTtsSpeaking,
                // Per-word tick straight from the TTS engine; pulses the
                // glow on every actual spoken word.
                speechTick     = uiState.ttsSpeechTick,
                onToggleMute   = {
                    // Flip the user-controlled mute. If we're now muted and
                    // STT is currently listening, stop it so the mic actually
                    // releases — otherwise the icon goes red but the mic
                    // stays open. Unmuting does NOT immediately restart STT;
                    // the auto-restart guard handles that on the next idle tick.
                    val nowMuted = !userMutedMic
                    userMutedMic = nowMuted
                    if (nowMuted && uiState.isRecording) {
                        viewModel.onIntent(ChatIntent.ToggleVoice)
                    }
                },
                onOpenSettings = { onNavigateToSettings() },
                onStop         = {
                    if (uiState.isRecording) viewModel.onIntent(ChatIntent.ToggleVoice)
                    viewModel.onIntent(ChatIntent.SetTtsEnabled(false))
                    // Wipe any half-captured STT partial so the chat input
                    // bar isn't pre-filled with whatever the user trailed
                    // off saying when they tapped Stop. Also reset the
                    // user-mute toggle so re-entering voice mode starts
                    // unmuted, not stuck in the previous session's state.
                    viewModel.onIntent(ChatIntent.UpdateInput(""))
                    userMutedMic = false
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

    // ── Rename conversation dialog ──────────────────────────────────────────
    renameConvId?.let { convId ->
        AlertDialog(
            onDismissRequest = { renameConvId = null },
            containerColor   = Color(0xFF141414),
            title = { Text("Rename conversation", color = TextPrimary) },
            text  = {
                androidx.compose.material3.OutlinedTextField(
                    value         = renameDraft,
                    onValueChange = { renameDraft = it },
                    singleLine    = true,
                    label         = { Text("Title") },
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        val title = renameDraft.trim()
                        if (title.isNotBlank()) {
                            viewModel.onIntent(ChatIntent.RenameConversation(convId, title))
                        }
                        renameConvId = null
                    },
                ) { Text("Save", color = Accent) }
            },
            dismissButton = {
                TextButton(onClick = { renameConvId = null }) {
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
    // Conversation context actions (top-right overflow). All operate on the
    // currently active conversation when one is selected; New chat is always
    // active.
    activeConversation:  com.jarvis.android.domain.model.Conversation?,
    onRename:            () -> Unit,
    onTogglePin:         () -> Unit,
    onDelete:            () -> Unit,
    onNewChat:           () -> Unit,
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
                // Claude-style conversation actions. Rename / Star / Delete
                // are gated on having an active conversation; New chat is
                // always available.
                val hasActive = activeConversation != null
                if (hasActive) {
                    DropdownMenuItem(
                        text    = { Text("Rename", color = TextPrimary) },
                        onClick = { toolMenuOpen = false; onRename() },
                    )
                    DropdownMenuItem(
                        text    = {
                            Text(
                                if (activeConversation?.isPinned == true) "Unstar" else "Star",
                                color = TextPrimary,
                            )
                        },
                        onClick = { toolMenuOpen = false; onTogglePin() },
                    )
                    DropdownMenuItem(
                        text    = { Text("Delete", color = Color(0xFFEF4444)) },
                        onClick = { toolMenuOpen = false; onDelete() },
                    )
                    androidx.compose.material3.HorizontalDivider(
                        color     = Color(0xFF2A2A2A),
                        thickness = 0.5.dp,
                    )
                }
                DropdownMenuItem(
                    text    = { Text("New chat", color = TextPrimary) },
                    onClick = { toolMenuOpen = false; onNewChat() },
                )
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
 * Which mic-using action the user just tapped. Held in ChatScreen state while
 * the system permission prompt is open so we can fire the right intent once
 * the user grants RECORD_AUDIO. There is no app-launch permission request any
 * more — see ChatScreen for the on-demand flow.
 */
private enum class MicAction { Dictate, VoiceMode }
