package com.jarvis.android.presentation.chat

import com.jarvis.android.domain.model.CloudModel
import com.jarvis.android.domain.model.Conversation
import com.jarvis.android.domain.model.Message
import com.jarvis.android.domain.model.ModelEntry
import com.jarvis.android.system.tools.ConfirmationRequest

/**
 * Immutable snapshot of everything [ChatScreen] needs to render.
 *
 * @param conversations        Full conversation list shown in the drawer.
 * @param activeConversationId Currently selected conversation, or null.
 * @param messages             Messages for the active conversation.
 * @param streamingText        Text accumulated during the current streaming turn.
 *                             Displayed as a "ghost" assistant bubble until [TurnSaved]
 *                             persists the real message and this is cleared.
 * @param isStreaming          True while an API turn is in flight.
 * @param activeToolCalls      Tools currently executing; shown in the streaming bubble.
 * @param inputText            Current contents of [JarvisInputBar].
 * @param pendingConfirmation  Non-null when the tool dispatcher needs user approval.
 * @param error                Non-null error message to surface as a Snackbar.
 * @param isLoadingHistory     True while the initial message list is being fetched.
 */
data class ChatUiState(
    val conversations:        List<Conversation>   = emptyList(),
    val activeConversationId: String?              = null,
    val messages:             List<Message>        = emptyList(),
    val streamingText:        String               = "",
    val isStreaming:          Boolean              = false,
    val activeToolCalls:      List<ActiveToolCall> = emptyList(),
    val inputText:            String               = "",
    val pendingConfirmation:  ConfirmationRequest? = null,
    val error:                String?              = null,
    val isLoadingHistory:     Boolean              = false,
    val isRecording:          Boolean              = false,
    val isTtsSpeaking:        Boolean              = false,
    /** Bumps every time the TTS engine starts speaking a new word. Drives
     *  per-word glow pulse in voice mode, the closest equivalent to real
     *  audio amplitude without the restricted Visualizer permission. */
    val ttsSpeechTick:        Long                 = 0L,
    // Off by default — when the user types they expect a text reply, not the
    // model talking back through the speaker. Voice-mode (the waveform circle
    // on the input bar) is the explicit opt-in for hands-free.
    val ttsEnabled:           Boolean              = false,
    val routingLabel:         String               = "Auto",
    /** Downloaded local models, shown as items in the top-bar dropdown. */
    val downloadedModels:     List<ModelEntry>     = emptyList(),
    /** Id of the model currently loaded into memory (null = none loaded). */
    val loadedLocalModelId:   String?              = null,
    /** Id of the model currently loading (drives the spinner on its menu row). */
    val loadingLocalModelId:  String?              = null,
    /**
     * Cloud models from providers whose API key is configured. Empty until
     * the user sets at least one provider key in Settings — the top-bar
     * dropdown only shows what the user can actually call.
     */
    val availableCloudModels: List<CloudModel>     = emptyList(),
    /** Anthropic / DeepSeek / etc. slug the user picked in the home-bar dropdown. */
    val selectedCloudModelId: String?              = null,
) {
    /** True when there is content to display (persisted messages or live streaming). */
    val hasContent: Boolean get() = messages.isNotEmpty() || streamingText.isNotEmpty()
}

/**
 * Represents a single in-flight or completed tool execution.
 * Rendered as a compact row inside the streaming assistant bubble.
 */
data class ActiveToolCall(
    val id:          String,
    val name:        String,
    val isCompleted: Boolean = false,
    val result:      String? = null,
    val isError:     Boolean = false,
)
