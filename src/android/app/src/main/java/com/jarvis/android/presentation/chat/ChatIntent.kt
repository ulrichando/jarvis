package com.jarvis.android.presentation.chat

/** All actions the user (or system) can trigger from [ChatScreen]. */
sealed class ChatIntent {

    // ── Messaging ─────────────────────────────────────────────────────────────

    /** Send the current [inputText] to the active conversation, optionally with a [imageBase64].
     *
     *  If [imageBase64] is null, the send path will pull any attachment that was
     *  previously staged via [StageImage] out of the UI state, so the normal
     *  "user picks image → chip → types prompt → hits send" flow works without
     *  the caller having to thread the bytes through by hand.
     */
    data class SendMessage(val imageBase64: String? = null) : ChatIntent()

    // ── Attachments (Claude-style staging) ────────────────────────────────────

    /**
     * Stage an image the user picked from the gallery / camera. Shows a chip
     * above the input bar; the image is only actually sent when the user taps
     * the send button (matches Claude Android behaviour — no surprise autosend).
     *
     * @param b64         Base64-encoded JPEG/PNG bytes, ready for the API.
     * @param mime        Source mime type (`image/jpeg`, `image/png`).
     * @param previewUri  `file://` URI of a local JPEG the UI can render as the
     *                    chip thumbnail and later as the inline image inside
     *                    the user bubble.
     */
    data class StageImage(
        val b64:        String,
        val mime:       String,
        val previewUri: String,
    ) : ChatIntent()

    /**
     * Stage a text-like file (plain text, source code, or extracted-PDF
     * text). The [content] stays OUT of the input field — we paste it for
     * the API only, and render a filename chip above the text field the
     * same way picture attachments work. The user types their prompt into
     * the clean input and sends.
     */
    data class StageFile(
        val fileName: String,
        val content:  String,
    ) : ChatIntent()

    /** Remove any currently-staged attachment (image or file chip). */
    object ClearAttachment : ChatIntent()

    /** Cancel the current streaming turn. */
    object StopStreaming : ChatIntent()

    /** Open the per-model Configurations dialog (gear icon in top bar). */
    object ShowModelConfig : ChatIntent()

    /** Close the Configurations dialog without saving. */
    object DismissModelConfig : ChatIntent()

    /** Save the edited model config back to prefs. */
    data class SaveModelConfig(
        val modelId: String,
        val config:  com.jarvis.android.domain.model.ModelConfig,
    ) : ChatIntent()

    /** Update the draft text in [JarvisInputBar]. */
    data class UpdateInput(val text: String) : ChatIntent()

    // ── Conversation management ───────────────────────────────────────────────

    /** Switch the active conversation to [id]. */
    data class SelectConversation(val id: String) : ChatIntent()

    /** Create a fresh conversation and make it active. */
    object NewConversation : ChatIntent()

    /** Permanently delete conversation [id] and all its messages. */
    data class DeleteConversation(val id: String) : ChatIntent()

    /** Rename conversation [id] to [title]. */
    data class RenameConversation(val id: String, val title: String) : ChatIntent()

    /** Toggle the pinned state of conversation [id]. */
    data class PinConversation(val id: String, val pinned: Boolean) : ChatIntent()

    // ── Tool confirmation ─────────────────────────────────────────────────────

    /** Resolve a pending tool confirmation dialog. */
    data class ResolveConfirmation(val requestId: String, val allowed: Boolean) : ChatIntent()

    // ── Voice / TTS / Routing ─────────────────────────────────────────────────

    /** Toggle voice input (microphone) recording. */
    object ToggleVoice : ChatIntent()

    /** Toggle TTS (text-to-speech) on/off. */
    object ToggleTts : ChatIntent()

    /** Set TTS to a specific value — used by voice mode to auto-enable when the
     *  overlay opens and auto-disable when it closes, so typed turns get
     *  text-only replies and voice-mode turns get spoken replies without the
     *  user manually toggling. */
    data class SetTtsEnabled(val enabled: Boolean) : ChatIntent()

    /** Speak [text] through TTS without changing history. Used by the ▶ button
     *  under an assistant turn so the user can replay a past response aloud. */
    data class ReplayResponse(val text: String) : ChatIntent()

    /** Delete the last assistant turn and re-stream a reply to the immediately
     *  preceding user turn. Used by the ⟲ regenerate icon under an assistant turn. */
    object RegenerateLast : ChatIntent()

    /** Cycle to the next routing mode (Auto → Local → Cloud → Hybrid → Auto). */
    object CycleRoutingMode : ChatIntent()

    /**
     * Pick a specific cloud (API) model from the home-bar dropdown.
     * Flips routing to CLOUD and records the chosen provider slug for the
     * next turn. When [id] is null, selects the provider default.
     */
    data class SelectCloudModel(val id: String) : ChatIntent()

    /**
     * Pick a specific downloaded local model from the home-bar dropdown.
     * Loads the model if it isn't already loaded, and flips routing to LOCAL.
     */
    data class SelectLocalModel(val id: String) : ChatIntent()

    // ── Error handling ────────────────────────────────────────────────────────

    /** Dismiss the current error Snackbar. */
    object ClearError : ChatIntent()
}
