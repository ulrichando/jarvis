package com.jarvis.android.service

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.AccessibilityServiceInfo
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Observes the active window's content tree and emits [AccessibilitySnapshot]s
 * that the AI agent can use to describe what is currently on screen.
 *
 * Configured via `res/xml/accessibility_service_config.xml`:
 *   - `eventTypes`: TYPE_WINDOW_STATE_CHANGED | TYPE_WINDOW_CONTENT_CHANGED
 *   - `feedbackType`: FEEDBACK_GENERIC
 *   - `flags`:        FLAG_REPORT_VIEW_IDS | FLAG_RETRIEVE_INTERACTIVE_WINDOWS
 *   - `notificationTimeout`: 100 ms
 *
 * ## Hilt note
 * [AccessibilityService] is bound by the OS before Hilt initialises, so
 * `@AndroidEntryPoint` cannot be used. The companion object exposes static
 * flows so consumers don't need a direct reference.
 *
 * ## Usage
 * ```kotlin
 * // Observe window changes
 * JarvisAccessibilityService.snapshots.collect { snap ->
 *     // snap.packageName, snap.windowTitle, snap.nodeTree
 * }
 *
 * // Perform a global action from the AI tool dispatcher
 * JarvisAccessibilityService.instance?.performGlobalAction(
 *     AccessibilityService.GLOBAL_ACTION_BACK
 * )
 * ```
 */
class JarvisAccessibilityService : AccessibilityService() {

    override fun onServiceConnected() {
        Log.i(TAG, "Accessibility service connected")
        serviceInfo = serviceInfo.apply {
            eventTypes = (
                AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED or
                AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED or
                AccessibilityEvent.TYPE_VIEW_CLICKED or
                AccessibilityEvent.TYPE_VIEW_FOCUSED
            )
            feedbackType       = AccessibilityServiceInfo.FEEDBACK_GENERIC
            notificationTimeout = 100L
            flags = (
                AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS or
                AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS
            )
        }
        _instance.value = this
        _connected.value = true
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        val type = event.eventType
        if (type == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED ||
            type == AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED) {
            emitSnapshot(event)
        }
    }

    override fun onInterrupt() {
        Log.w(TAG, "Accessibility service interrupted")
    }

    override fun onDestroy() {
        Log.i(TAG, "Accessibility service destroyed")
        _instance.value = null
        _connected.value = false
        super.onDestroy()
    }

    // ── Snapshot builder ──────────────────────────────────────────────────

    private fun emitSnapshot(event: AccessibilityEvent) {
        try {
            val pkg   = event.packageName?.toString() ?: return
            val title = event.text.joinToString(" ").ifBlank {
                rootInActiveWindow?.findFocus(AccessibilityNodeInfo.FOCUS_ACCESSIBILITY)
                    ?.text?.toString() ?: ""
            }
            val nodeTree = rootInActiveWindow?.let { buildNodeTree(it) }

            val snapshot = AccessibilitySnapshot(
                packageName  = pkg,
                windowTitle  = title,
                eventType    = event.eventType,
                nodeTree     = nodeTree,
            )
            _snapshots.tryEmit(snapshot)
        } catch (e: Exception) {
            Log.w(TAG, "emitSnapshot error: ${e.message}")
        }
    }

    private fun buildNodeTree(node: AccessibilityNodeInfo, depth: Int = 0): AccessibilityNode {
        val children = mutableListOf<AccessibilityNode>()
        if (depth < MAX_TREE_DEPTH) {
            for (i in 0 until node.childCount) {
                node.getChild(i)?.let { children.add(buildNodeTree(it, depth + 1)) }
            }
        }
        return AccessibilityNode(
            className   = node.className?.toString(),
            text        = node.text?.toString(),
            contentDesc = node.contentDescription?.toString(),
            viewIdRes   = node.viewIdResourceName,
            isClickable = node.isClickable,
            isEditable  = node.isEditable,
            isFocused   = node.isFocused,
            isChecked   = node.isChecked,
            children    = children,
        )
    }

    companion object {
        private const val TAG           = "JarvisAccessibility"
        private const val MAX_TREE_DEPTH = 5

        private val _instance  = MutableStateFlow<JarvisAccessibilityService?>(null)
        /** Live reference to the running service instance, or null when disabled. */
        val instance: JarvisAccessibilityService? get() = _instance.value

        private val _connected = MutableStateFlow(false)
        /** True when the accessibility service is connected and active. */
        val connected: StateFlow<Boolean> = _connected.asStateFlow()

        private val _snapshots = MutableSharedFlow<AccessibilitySnapshot>(
            extraBufferCapacity = 8,
            onBufferOverflow = BufferOverflow.DROP_OLDEST,
        )
        /**
         * Emits an [AccessibilitySnapshot] on every meaningful window change.
         * The AI tool layer can collect this to answer "what's on screen?" queries.
         */
        val snapshots: SharedFlow<AccessibilitySnapshot> = _snapshots.asSharedFlow()
    }
}

// ── Data types ────────────────────────────────────────────────────────────────

/** A point-in-time snapshot of the foreground window's accessibility tree. */
data class AccessibilitySnapshot(
    val packageName: String,
    val windowTitle: String,
    val eventType:   Int,
    val nodeTree:    AccessibilityNode?,
)

/**
 * A single node in the accessibility view hierarchy.
 * Depth-limited to [JarvisAccessibilityService.MAX_TREE_DEPTH] to bound allocations.
 */
data class AccessibilityNode(
    val className:   String?,
    val text:        String?,
    val contentDesc: String?,
    val viewIdRes:   String?,
    val isClickable: Boolean,
    val isEditable:  Boolean,
    val isFocused:   Boolean,
    val isChecked:   Boolean,
    val children:    List<AccessibilityNode>,
)
