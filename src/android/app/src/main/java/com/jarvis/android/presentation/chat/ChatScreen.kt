package com.jarvis.android.presentation.chat

import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.runtime.DisposableEffect
import kotlin.math.PI
import kotlin.math.sin
import androidx.compose.foundation.Canvas
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
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Build
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.BottomSheetScaffold
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.rememberBottomSheetScaffoldState
import androidx.compose.material3.rememberDrawerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import com.jarvis.android.R
import com.jarvis.android.domain.model.Message
import com.jarvis.android.domain.model.MessageContentType
import com.jarvis.android.domain.model.MessageRole
import com.jarvis.android.presentation.components.ConversationDrawer
import com.jarvis.android.presentation.components.JarvisInputBar
import com.jarvis.android.presentation.components.MessageBubble
import com.jarvis.android.presentation.components.StreamingCursor
import kotlinx.coroutines.launch

// ── Design tokens ─────────────────────────────────────────────────────────────

private val VoiceBg       = Color(0xFF000000)
private val ChatBg        = Color(0xFF0D0D0D)
private val CardBg        = Color(0xFF212121)
private val BorderSubtle  = Color(0xFF2E2E2E)
private val TextPrimary   = Color(0xFFD9D9D9)
private val TextSecondary = Color(0xFF7E7E7E)
private val TextMuted     = Color(0xFF999999)
private val AiBlue        = Color(0xFF1E7FFF)

private val SuggestedQuestions = listOf(
    "What can you help me with?",
    "Scan the local network for open ports",
    "Show me running processes on this device",
    "Analyze recent system log entries",
    "Help me write a shell script",
)

/**
 * Root screen of the JARVIS experience.
 *
 * ## Architecture
 * ```
 * ModalNavigationDrawer
 *   └── BottomSheetScaffold
 *        ├── content: VoiceScreen (always behind the sheet)
 *        │     - Particle sphere image (Figma node 1:16)
 *        │     - Blue when [isStreaming]: sphere tints blue, wave rings expand,
 *        │       glow pulses — i.e. the sphere "moves" with the AI voice
 *        └── sheet: ChatOverlay (swipe up to reveal text chat)
 *              ├── CenterAlignedTopAppBar
 *              ├── EmptyState or MessageList
 *              └── JarvisInputBar
 * ```
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
    val uiState            by viewModel.uiState.collectAsState()
    val drawerState        = rememberDrawerState(DrawerValue.Closed)
    val sheetScaffoldState = rememberBottomSheetScaffoldState()
    val scope              = rememberCoroutineScope()
    val snackbarState      = remember { SnackbarHostState() }
    val listState          = rememberLazyListState()

    // Long-press delete confirmation
    var deleteConvId by remember { mutableStateOf<String?>(null) }
    var deleteConvTitle by remember { mutableStateOf("") }

    LaunchedEffect(uiState.error) {
        uiState.error?.let {
            snackbarState.showSnackbar(it)
            viewModel.onIntent(ChatIntent.ClearError)
        }
    }

    val messageCount = uiState.messages.size
    val streamLen    = uiState.streamingText.length
    LaunchedEffect(messageCount, streamLen > 0) {
        val total = messageCount + if (uiState.streamingText.isNotEmpty()) 1 else 0
        if (total > 0) listState.animateScrollToItem(total - 1)
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
        BottomSheetScaffold(
            scaffoldState       = sheetScaffoldState,
            sheetPeekHeight     = 0.dp,
            sheetShape          = RoundedCornerShape(topStart = 24.dp, topEnd = 24.dp),
            sheetContainerColor = ChatBg,
            sheetContentColor   = TextPrimary,
            sheetDragHandle     = null,
            snackbarHost        = { SnackbarHost(snackbarState) },
            sheetContent = {
                ChatOverlay(
                    uiState             = uiState,
                    listState           = listState,
                    onCollapse          = { scope.launch { sheetScaffoldState.bottomSheetState.partialExpand() } },
                    onNewConversation   = { viewModel.onIntent(ChatIntent.NewConversation) },
                    onSuggestedQuestion = { text ->
                        viewModel.onIntent(ChatIntent.UpdateInput(text))
                        viewModel.onIntent(ChatIntent.SendMessage())
                    },
                    onSend         = { viewModel.onIntent(ChatIntent.SendMessage()) },
                    onStop         = { viewModel.onIntent(ChatIntent.StopStreaming) },
                    onTextChange   = { viewModel.onIntent(ChatIntent.UpdateInput(it)) },
                    onVoice        = { viewModel.onIntent(ChatIntent.ToggleVoice) },
                    onToggleTts    = { viewModel.onIntent(ChatIntent.ToggleTts) },
                    onCycleRouting = { viewModel.onIntent(ChatIntent.CycleRoutingMode) },
                )
            },
        ) { _ ->
            VoiceScreen(
                isAiSpeaking  = uiState.isStreaming || uiState.isTtsSpeaking,
                onMenuClick   = { scope.launch { drawerState.open() } },
                onNewChat     = {
                    viewModel.onIntent(ChatIntent.NewConversation)
                    scope.launch { sheetScaffoldState.bottomSheetState.expand() }
                },
                onTerminal    = onNavigateToTerminal,
                onFiles       = onNavigateToFiles,
                onSystem      = onNavigateToSystem,
                onNetwork     = onNavigateToNetwork,
                onSensors     = onNavigateToSensors,
                onPermissions = onNavigateToPermissions,
                onSettings    = onNavigateToSettings,
                onLocalAi     = onNavigateToLocalAi,
                onAppBuilder  = onNavigateToAppBuilder,
                onCyberSuite  = onNavigateToCyberSuite,
                modifier      = Modifier.fillMaxSize(),
            )
        }
    }

    // Delete conversation confirmation dialog
    deleteConvId?.let { convId ->
        AlertDialog(
            onDismissRequest = { deleteConvId = null },
            title = { Text("Delete conversation?") },
            text  = { Text("\"$deleteConvTitle\" will be permanently deleted.") },
            confirmButton = {
                TextButton(onClick = {
                    viewModel.onIntent(ChatIntent.DeleteConversation(convId))
                    deleteConvId = null
                }) { Text("Delete", color = Color(0xFFCF4A3C)) }
            },
            dismissButton = {
                TextButton(onClick = { deleteConvId = null }) { Text("Cancel") }
            },
        )
    }

    uiState.pendingConfirmation?.let { req ->
        AlertDialog(
            onDismissRequest = {
                viewModel.onIntent(ChatIntent.ResolveConfirmation(req.id, allowed = false))
            },
            title = { Text("Confirm: ${req.description}") },
            text  = {
                Column {
                    Text(req.description)
                    Spacer(Modifier.height(8.dp))
                    Text(req.detail, style = MaterialTheme.typography.bodySmall, color = TextSecondary)
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    viewModel.onIntent(ChatIntent.ResolveConfirmation(req.id, allowed = true))
                }) { Text("Allow") }
            },
            dismissButton = {
                TextButton(onClick = {
                    viewModel.onIntent(ChatIntent.ResolveConfirmation(req.id, allowed = false))
                }) { Text("Deny") }
            },
        )
    }
}

// ── Voice screen ──────────────────────────────────────────────────────────────

/**
 * Full-screen voice visualization, always rendered behind the chat overlay.
 *
 * ## Bar animation source (priority order)
 * 1. **Real audio** — [AudioAmplitudeMonitor] via [Visualizer] on the primary mix.
 *    Active whenever the screen is visible. Reacts to TTS, media, or any output audio.
 * 2. **Simulated sine** — when [isAiSpeaking] but the Visualizer returns silence
 *    (e.g. text-only streaming with no TTS). Two overlapping sine waves at
 *    different frequencies produce organic, speech-like motion.
 * 3. **Idle stubs** — all bars at minimum height when nothing is happening.
 *
 * ## Other animations
 * - Sphere breathes (scale 1.0 → 1.08) while [isAiSpeaking].
 * - Sphere gets a blue `Screen` tint proportional to [amplitude].
 * - Bottom glow pulses in intensity when speaking.
 */
@Composable
private fun VoiceScreen(
    isAiSpeaking:  Boolean,
    onMenuClick:   () -> Unit,
    onNewChat:     () -> Unit,
    onTerminal:    () -> Unit,
    onFiles:       () -> Unit,
    onSystem:      () -> Unit,
    onNetwork:     () -> Unit,
    onSensors:     () -> Unit,
    onPermissions: () -> Unit,
    onSettings:    () -> Unit,
    onLocalAi:     () -> Unit,
    onAppBuilder:  () -> Unit,
    onCyberSuite:  () -> Unit,
    modifier:      Modifier = Modifier,
) {
    var showVoiceMenu by remember { mutableStateOf(false) }

    // ── Real audio monitor (Visualizer API) ───────────────────────────────
    val monitor    = remember { AudioAmplitudeMonitor() }
    val audioAmps  by monitor.amplitudes.collectAsState()

    DisposableEffect(Unit) {
        monitor.start()
        onDispose { monitor.stop() }
    }

    // True when the Visualizer is returning real non-silent audio
    val hasRealAudio = audioAmps.max() > 0.06f
    val inf = rememberInfiniteTransition(label = "voice")

    // Continuous time for bar sine-wave animation (0 → 2π × 4 over 4 s)
    val waveTime by inf.animateFloat(
        initialValue  = 0f,
        targetValue   = (2.0 * PI * 4.0).toFloat(),
        animationSpec = infiniteRepeatable(tween(4_000, easing = LinearEasing)),
        label         = "waveTime",
    )

    // Bottom glow — pulses only when AI speaks
    val glowPulse by inf.animateFloat(
        initialValue  = 0.45f,
        targetValue   = 0.90f,
        animationSpec = infiniteRepeatable(
            tween(900, easing = FastOutSlowInEasing),
            RepeatMode.Reverse,
        ),
        label = "glow",
    )

    // Smooth amplitude: 0 = idle, 1 = speaking
    val amplitude by animateFloatAsState(
        targetValue   = if (isAiSpeaking) 1f else 0f,
        animationSpec = tween(500, easing = FastOutSlowInEasing),
        label         = "amplitude",
    )

    // Glow ONLY when AI is speaking — amplitude smoothly gates the pulse
    val glowAlpha = glowPulse * amplitude

    Box(modifier = modifier.background(VoiceBg)) {

        // ── Three.js holographic sphere (same as desktop ArcReactor) ─────────
        ArcReactorWebView(
            isAiSpeaking = isAiSpeaking,
            audioLevel   = if (hasRealAudio) audioAmps.average().toFloat() else amplitude * 0.3f,
            modifier     = Modifier
                .fillMaxWidth()
                .aspectRatio(1f)
                .align(Alignment.Center),
        )

        // ── Voice bars — centered horizontally, below the sphere ─────────
        //
        // 28 bars animated with overlapping sine waves at different phases.
        // When idle (amplitude ≈ 0) bars are just thin stubs.
        // When speaking (amplitude = 1) bars pulse up and down rapidly.
        Canvas(modifier = Modifier.fillMaxSize()) {
            val numBars    = 28
            val barW       = 5.dp.toPx()
            val gap        = 4.dp.toPx()
            val totalW     = numBars * barW + (numBars - 1) * gap
            val startX     = (size.width - totalW) / 2f
            // Position bars at ~65% down the screen (below the sphere)
            val barCenterY = size.height * 0.65f
            val maxH       = 48.dp.toPx()
            val minH       = 4.dp.toPx()
            val cornerR    = barW / 2f

            for (i in 0 until numBars) {
                // Use real Visualizer data when available; otherwise fall back
                // to two overlapping sine waves for organic simulated motion.
                val norm: Float = if (hasRealAudio) {
                    audioAmps[i].coerceIn(0f, 1f)
                } else {
                    val t     = waveTime
                    val phase = i * 0.40f
                    val wave  = sin(t * 1.8f + phase) * 0.55f +
                                sin(t * 3.1f + phase * 1.3f) * 0.45f
                    // wave ∈ [-1, 1]; normalise to [0, 1]
                    (wave + 1f) / 2f
                }
                // Multiply the whole height by amplitude so bars vanish at idle
                val barH    = (minH + (maxH - minH) * norm) * amplitude

                val x = startX + i * (barW + gap)
                drawRoundRect(
                    color        = AiBlue.copy(alpha = (0.5f + 0.5f * norm) * (0.3f + 0.7f * amplitude)),
                    topLeft      = Offset(x, barCenterY - barH / 2f),
                    size         = androidx.compose.ui.geometry.Size(barW, barH),
                    cornerRadius = androidx.compose.ui.geometry.CornerRadius(cornerR),
                )
            }
        }

        // ── Blue glow — only when AI is speaking, pulses with speech ────────
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(220.dp)
                .align(Alignment.BottomCenter)
                .background(
                    brush = Brush.verticalGradient(
                        colors = listOf(
                            Color.Transparent,
                            AiBlue.copy(alpha = glowAlpha * 0.72f),
                        ),
                    ),
                ),
        )

        // ── Top bar: [hamburger] ─────────────── [+ new chat] [⋮ nav] ───────
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .statusBarsPadding()
                .padding(horizontal = 4.dp, vertical = 4.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment     = Alignment.CenterVertically,
        ) {
            IconButton(onClick = onMenuClick) {
                Icon(Icons.Default.Menu, "Open menu",
                    tint = Color.White.copy(alpha = 0.85f), modifier = Modifier.size(24.dp))
            }
            Row {
                IconButton(onClick = onNewChat) {
                    Icon(Icons.Default.Add, "New chat",
                        tint = Color.White.copy(alpha = 0.85f), modifier = Modifier.size(24.dp))
                }
                Box {
                    IconButton(onClick = { showVoiceMenu = true }) {
                        Icon(Icons.Default.MoreVert, "More options",
                            tint = Color.White.copy(alpha = 0.85f), modifier = Modifier.size(24.dp))
                    }
                    DropdownMenu(
                        expanded         = showVoiceMenu,
                        onDismissRequest = { showVoiceMenu = false },
                        modifier         = Modifier.background(Color(0xFF1C1C1E)),
                    ) {
                        DropdownMenuItem(text = { Text("Terminal",    color = TextPrimary) }, onClick = { showVoiceMenu = false; onTerminal() })
                        DropdownMenuItem(text = { Text("File Manager",color = TextPrimary) }, onClick = { showVoiceMenu = false; onFiles() })
                        DropdownMenuItem(text = { Text("System",      color = TextPrimary) }, onClick = { showVoiceMenu = false; onSystem() })
                        DropdownMenuItem(text = { Text("Network",     color = TextPrimary) }, onClick = { showVoiceMenu = false; onNetwork() })
                        DropdownMenuItem(text = { Text("Sensors",     color = TextPrimary) }, onClick = { showVoiceMenu = false; onSensors() })
                        DropdownMenuItem(text = { Text("Permissions", color = TextPrimary) }, onClick = { showVoiceMenu = false; onPermissions() })
                        DropdownMenuItem(text = { Text("Settings",    color = TextPrimary) }, onClick = { showVoiceMenu = false; onSettings() })
                        DropdownMenuItem(text = { Text("Local AI",    color = TextPrimary) }, onClick = { showVoiceMenu = false; onLocalAi() })
                        DropdownMenuItem(text = { Text("App Builder", color = TextPrimary) }, onClick = { showVoiceMenu = false; onAppBuilder() })
                        DropdownMenuItem(text = { Text("Cyber Suite", color = TextPrimary) }, onClick = { showVoiceMenu = false; onCyberSuite() })
                    }
                }
            }
        }
    }
}

// ── Chat overlay (bottom sheet content) ──────────────────────────────────────

@Composable
private fun ChatOverlay(
    uiState:             ChatUiState,
    listState:           androidx.compose.foundation.lazy.LazyListState,
    onCollapse:          () -> Unit,
    onNewConversation:   () -> Unit,
    onSuggestedQuestion: (String) -> Unit,
    onSend:              () -> Unit,
    onStop:              () -> Unit,
    onTextChange:        (String) -> Unit,
    onVoice:             () -> Unit,
    onToggleTts:         () -> Unit,
    onCycleRouting:      () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(ChatBg),
    ) {
        ChatTopBar(
            onCollapse        = onCollapse,
            onNewConversation = onNewConversation,
        )

        Box(modifier = Modifier.weight(1f)) {
            if (!uiState.hasContent) {
                EmptyStateContent(
                    onSuggestedQuestion = onSuggestedQuestion,
                    modifier            = Modifier.fillMaxSize(),
                )
            } else {
                MessageList(
                    messages        = uiState.messages,
                    streamingText   = uiState.streamingText,
                    activeToolCalls = uiState.activeToolCalls,
                    isStreaming     = uiState.isStreaming,
                    listState       = listState,
                    modifier        = Modifier.fillMaxSize(),
                )
            }
        }

        JarvisInputBar(
            text           = uiState.inputText,
            onTextChange   = onTextChange,
            onSend         = onSend,
            onStop         = onStop,
            isStreaming    = uiState.isStreaming,
            enabled        = true,
            onVoice        = onVoice,
            isRecording    = uiState.isRecording,
            ttsEnabled     = uiState.ttsEnabled,
            onToggleTts    = onToggleTts,
            routingLabel   = uiState.routingLabel,
            onCycleRouting = onCycleRouting,
            modifier       = Modifier.imePadding(),
        )
    }
}

// ── Empty state ───────────────────────────────────────────────────────────────

@Composable
private fun EmptyStateContent(
    onSuggestedQuestion: (String) -> Unit,
    modifier:            Modifier = Modifier,
) {
    Column(modifier = modifier.padding(horizontal = 20.dp)) {
        Column(
            modifier                = Modifier.fillMaxWidth().weight(1f),
            horizontalAlignment     = Alignment.CenterHorizontally,
            verticalArrangement     = Arrangement.Center,
        ) {
            Icon(
                painter            = painterResource(R.drawable.ic_jarvis_notification),
                contentDescription = "JARVIS",
                tint               = AiBlue,
                modifier           = Modifier.size(57.dp, 64.dp),
            )
            Spacer(Modifier.height(12.dp))
            Text(
                text      = "Hey, JARVIS is here!",
                style     = MaterialTheme.typography.titleLarge.copy(fontWeight = FontWeight.Bold),
                color     = TextPrimary,
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(8.dp))
            Text(
                text      = "Let me help you find clarity in seconds.",
                style     = MaterialTheme.typography.bodyMedium,
                color     = TextSecondary,
                textAlign = TextAlign.Center,
            )
        }

        Column(
            modifier            = Modifier.fillMaxWidth().padding(bottom = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(
                text  = "✨ Ask me anything",
                style = MaterialTheme.typography.bodyLarge.copy(fontWeight = FontWeight.Medium),
                color = TextPrimary,
            )
            Spacer(Modifier.height(4.dp))
            SuggestedQuestions.forEach { q ->
                SuggestedQuestionPill(text = q, onClick = { onSuggestedQuestion(q) })
            }
        }
    }
}

@Composable
private fun SuggestedQuestionPill(text: String, onClick: () -> Unit, modifier: Modifier = Modifier) {
    Box(
        modifier = modifier
            .background(CardBg, RoundedCornerShape(12.dp))
            .border(1.dp, BorderSubtle, RoundedCornerShape(12.dp))
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 6.dp),
    ) {
        Text(text = text, style = MaterialTheme.typography.bodySmall, color = TextMuted)
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
        contentPadding      = PaddingValues(vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        items(items = messages, key = { it.id }) { msg ->
            MessageBubble(message = msg, modifier = Modifier.padding(horizontal = 8.dp))
        }

        if (activeToolCalls.isNotEmpty()) {
            item(key = "tool_strip") {
                ToolCallStrip(
                    toolCalls = activeToolCalls,
                    modifier  = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                )
            }
        }

        if (isStreaming && streamingText.isNotEmpty()) {
            item(key = "streaming") {
                StreamingBubble(text = streamingText, modifier = Modifier.padding(horizontal = 8.dp))
            }
        } else if (isStreaming) {
            item(key = "thinking") {
                ThinkingIndicator(modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp))
            }
        }
    }
}

@Composable
private fun StreamingBubble(text: String, modifier: Modifier = Modifier) {
    val ghost = remember(text) {
        Message(
            id = -1L, conversationId = "", role = MessageRole.ASSISTANT,
            content = text, contentType = MessageContentType.TEXT,
            toolCallsJson = null, timestamp = System.currentTimeMillis(),
            inputTokens = 0, outputTokens = 0, stopReason = null, isOffline = false,
        )
    }
    MessageBubble(message = ghost, streamingText = text, isStreaming = true, modifier = modifier)
}

@Composable
private fun ToolCallStrip(toolCalls: List<ActiveToolCall>, modifier: Modifier = Modifier) {
    Column(modifier = modifier) {
        toolCalls.forEach { tc ->
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(vertical = 2.dp)) {
                Icon(imageVector = Icons.Default.Build, contentDescription = null,
                    tint = if (tc.isError) MaterialTheme.colorScheme.error else AiBlue,
                    modifier = Modifier.size(14.dp))
                Spacer(Modifier.width(6.dp))
                Text(
                    text  = if (tc.isCompleted) "✓ ${tc.name}" else "⟳ ${tc.name}",
                    style = MaterialTheme.typography.labelSmall,
                    color = if (tc.isError) MaterialTheme.colorScheme.error else TextSecondary,
                )
            }
        }
    }
}

@Composable
private fun ThinkingIndicator(modifier: Modifier = Modifier) {
    Row(modifier = modifier, verticalAlignment = Alignment.CenterVertically) {
        Text(text = "Still thinking", style = MaterialTheme.typography.bodySmall, color = TextSecondary)
        Spacer(Modifier.width(4.dp))
        StreamingCursor()
    }
}

// ── Chat top bar ──────────────────────────────────────────────────────────────

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ChatTopBar(
    onCollapse:        () -> Unit,
    onNewConversation: () -> Unit,
) {
    CenterAlignedTopAppBar(
        title = {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Icon(painterResource(R.drawable.ic_jarvis_notification), null, tint = AiBlue,
                    modifier = Modifier.size(22.dp, 25.dp))
                Text("JARVIS", style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold),
                    color = TextPrimary)
            }
        },
        navigationIcon = {
            // Collapse back to the voice screen
            IconButton(onClick = onCollapse) {
                Icon(Icons.Default.KeyboardArrowDown, "Back to voice", tint = TextPrimary)
            }
        },
        actions = {
            IconButton(onClick = onNewConversation) {
                Icon(Icons.Default.Add, "New conversation", tint = TextPrimary)
            }
        },
        colors = TopAppBarDefaults.centerAlignedTopAppBarColors(containerColor = ChatBg),
    )
}
