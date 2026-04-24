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

    /**
     * When true, the ModelConfigDialog is rendered. `editingModelConfig`
     * holds the values currently shown/edited. Set by [ChatIntent.ShowModelConfig]
     * from the gear icon in the chat top bar; cleared on dismiss/save.
     */
    val showModelConfig:      Boolean              = false,
    val editingModelConfig:   com.jarvis.android.domain.model.ModelConfig? = null,

    // ── Attachments ────────────────────────────────────────────────────────
    //
    // Picking a photo or file no longer auto-sends — it stages into the state
    // below. The input bar renders a chip off [pendingImagePreviewUri] /
    // [pendingFileName] so the user actually sees what they attached, and
    // then hits Send when they're ready.
    /** Base64 JPEG/PNG payload staged for the next send, null if none. */
    val pendingImageB64:        String?            = null,
    /** Mime type of the staged image, `image/jpeg` by default. */
    val pendingImageMime:       String             = "image/jpeg",
    /** `file://` URI of the locally cached preview, loaded by Coil for the chip. */
    val pendingImagePreviewUri: String?            = null,
    /** Display name of a staged file attachment — drives the filename chip. */
    val pendingFileName:        String?            = null,
    /** Full text extracted from the staged file. Prepended to the next
     *  outgoing request as context and never shown in the input field. */
    val pendingFileContent:     String?            = null,

    /**
     * After a user turn with an image is persisted, we record its DB row id
     * against the local preview URI here. [MessageBubble] reads this map at
     * render time and shows the image inline above its text. Transient —
     * populated per session; images do not survive a process restart yet.
     */
    val sentImagePaths:         Map<Long, String>  = emptyMap(),

    /**
     * Same idea as [sentImagePaths] but for text/file attachments: DB row
     * id → filename. Lets the bubble render a file-chip above the user's
     * typed prompt.
     */
    val sentFileNames:          Map<Long, String>  = emptyMap(),
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
