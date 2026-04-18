package com.jarvis.android.presentation.chat

/** All actions the user (or system) can trigger from [ChatScreen]. */
sealed class ChatIntent {

    // ── Messaging ─────────────────────────────────────────────────────────────

    /** Send the current [inputText] to the active conversation, optionally with a [imageBase64]. */
    data class SendMessage(val imageBase64: String? = null) : ChatIntent()

    /** Cancel the current streaming turn. */
    object StopStreaming : ChatIntent()

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

    /** Cycle to the next routing mode (Auto → Local → Cloud → Hybrid → Auto). */
    object CycleRoutingMode : ChatIntent()

    // ── Error handling ────────────────────────────────────────────────────────

    /** Dismiss the current error Snackbar. */
    object ClearError : ChatIntent()
}
