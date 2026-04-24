package com.jarvis.android.service

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.AccessibilityServiceInfo
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.graphics.Rect
import android.os.Bundle
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import kotlinx.coroutines.CompletableDeferred
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
        val bounds = Rect().also { node.getBoundsInScreen(it) }
        return AccessibilityNode(
            className   = node.className?.toString(),
            text        = node.text?.toString(),
            contentDesc = node.contentDescription?.toString(),
            viewIdRes   = node.viewIdResourceName,
            isClickable = node.isClickable,
            isEditable  = node.isEditable,
            isFocused   = node.isFocused,
            isChecked   = node.isChecked,
            bounds      = bounds,
            children    = children,
        )
    }

    // ── Public helpers used by JarvisToolDispatcher ──────────────────────────
    //
    // All these are best-effort: they return a human-readable string that the
    // AI can inspect and retry on, instead of throwing. Exceptions are caught
    // and surfaced with the tool result.

    /** Dump the current UI tree as an indented text summary. */
    fun dumpUi(maxDepth: Int = MAX_TREE_DEPTH): String {
        val root = rootInActiveWindow ?: return "error: no active window"
        val pkg  = root.packageName?.toString() ?: "?"
        val sb   = StringBuilder()
        sb.appendLine("package: $pkg")
        val counter = intArrayOf(0)
        dumpNode(root, 0, maxDepth, sb, counter)
        return sb.toString().trimEnd()
    }

    private fun dumpNode(
        node: AccessibilityNodeInfo,
        depth: Int,
        maxDepth: Int,
        sb: StringBuilder,
        counter: IntArray,
    ) {
        val idx = counter[0]++
        val indent = "  ".repeat(depth)
        val cls    = node.className?.toString()?.substringAfterLast('.') ?: "?"
        val text   = node.text?.toString()?.take(60)
        val desc   = node.contentDescription?.toString()?.take(60)
        val vid    = node.viewIdResourceName?.substringAfterLast('/')
        val b      = Rect().also { node.getBoundsInScreen(it) }
        val flags  = buildString {
            if (node.isClickable) append('C')
            if (node.isEditable)  append('E')
            if (node.isFocused)   append('F')
            if (node.isChecked)   append('✓')
            if (node.isScrollable) append('S')
        }

        sb.append(indent).append("#$idx <$cls>")
        if (!text.isNullOrBlank()) sb.append(" \"").append(text).append('"')
        if (!desc.isNullOrBlank()) sb.append(" desc=\"").append(desc).append('"')
        if (!vid.isNullOrBlank())  sb.append(" id=").append(vid)
        if (flags.isNotEmpty())    sb.append(" [").append(flags).append(']')
        sb.append(" @[${b.left},${b.top},${b.right},${b.bottom}]\n")

        if (depth < maxDepth) {
            for (i in 0 until node.childCount) {
                node.getChild(i)?.let { dumpNode(it, depth + 1, maxDepth, sb, counter) }
            }
        }
    }

    /** Find the first interactive node matching [text] (case-insensitive, substring). */
    fun findByText(text: String, clickableOnly: Boolean = false): AccessibilityNodeInfo? {
        val root = rootInActiveWindow ?: return null
        val needle = text.lowercase()
        return findFirst(root) { n ->
            val t = n.text?.toString()?.lowercase().orEmpty()
            val d = n.contentDescription?.toString()?.lowercase().orEmpty()
            val matches = needle in t || needle in d
            matches && (!clickableOnly || isNodeTappable(n))
        }
    }

    private fun findFirst(
        root: AccessibilityNodeInfo,
        pred: (AccessibilityNodeInfo) -> Boolean,
    ): AccessibilityNodeInfo? {
        val stack = ArrayDeque<AccessibilityNodeInfo>()
        stack.addLast(root)
        while (stack.isNotEmpty()) {
            val n = stack.removeLast()
            if (pred(n)) return n
            for (i in 0 until n.childCount) n.getChild(i)?.let(stack::addLast)
        }
        return null
    }

    private fun isNodeTappable(n: AccessibilityNodeInfo): Boolean {
        // Walk ancestors: if the view or any parent is clickable, a tap on
        // this node's centre will reach a handler. Handles the common case
        // where only the container is clickable but a TextView is the
        // visually-distinct target.
        var cur: AccessibilityNodeInfo? = n
        repeat(6) {
            if (cur == null) return false
            if (cur!!.isClickable) return true
            cur = cur!!.parent
        }
        return false
    }

    /** Tap by text/contentDesc: clicks the node (or the centre of its bounds). */
    fun tapByText(text: String): String {
        val node = findByText(text, clickableOnly = true)
            ?: findByText(text, clickableOnly = false)
            ?: return "error: no visible node matches '$text'"
        return tapNode(node, text)
    }

    private fun tapNode(node: AccessibilityNodeInfo, label: String): String {
        // Prefer ACTION_CLICK on the clickable ancestor — more reliable than
        // a synthetic gesture because it respects the view's actual handler.
        var cur: AccessibilityNodeInfo? = node
        repeat(6) {
            if (cur == null) return@repeat
            if (cur!!.isClickable && cur!!.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
                return "tapped: '$label' via ACTION_CLICK"
            }
            cur = cur!!.parent
        }
        // Fallback: synthesise a tap at the centre of the original node.
        val b = Rect().also { node.getBoundsInScreen(it) }
        return tapAt(b.centerX().toFloat(), b.centerY().toFloat(), 80)
            .let { "tapped: '$label' via gesture ($it)" }
    }

    /** Synthesise a one-shot tap at (x,y). [durationMs] tunes press length. */
    fun tapAt(x: Float, y: Float, durationMs: Long = 80): String {
        val path = Path().apply { moveTo(x, y) }
        return dispatchStroke(path, 0L, durationMs, "tap($x,$y)")
    }

    /** Long-press: same as tap but ~650 ms duration. */
    fun longPressAt(x: Float, y: Float): String = tapAt(x, y, durationMs = 650)

    /** Swipe between two points over [durationMs]. */
    fun swipe(x1: Float, y1: Float, x2: Float, y2: Float, durationMs: Long = 300): String {
        val path = Path().apply {
            moveTo(x1, y1)
            lineTo(x2, y2)
        }
        return dispatchStroke(path, 0L, durationMs, "swipe($x1,$y1→$x2,$y2)")
    }

    private fun dispatchStroke(
        path: Path,
        startAt: Long,
        durationMs: Long,
        label: String,
    ): String {
        val stroke   = GestureDescription.StrokeDescription(path, startAt, durationMs)
        val gesture  = GestureDescription.Builder().addStroke(stroke).build()
        val done     = CompletableDeferred<Boolean>()
        val ok = dispatchGesture(
            gesture,
            object : GestureResultCallback() {
                override fun onCompleted(g: GestureDescription?) { done.complete(true) }
                override fun onCancelled(g: GestureDescription?) { done.complete(false) }
            },
            null,
        )
        if (!ok) return "error: dispatchGesture returned false for $label"
        return "ok: $label"
    }

    /** Input text into the currently focused editable node. */
    fun inputText(text: String): String {
        val root = rootInActiveWindow ?: return "error: no active window"
        val editable = findFirst(root) { it.isFocused && it.isEditable }
            ?: findFirst(root) { it.isEditable }
            ?: return "error: no editable field on screen"
        val args = Bundle().apply {
            putCharSequence(
                AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                text,
            )
        }
        return if (editable.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args))
            "ok: typed ${text.length} chars"
        else "error: ACTION_SET_TEXT refused"
    }

    /** Perform a named global action (back/home/recents/notifications/quick-settings). */
    fun globalAction(name: String): String {
        val action = when (name.lowercase()) {
            "back"            -> GLOBAL_ACTION_BACK
            "home"            -> GLOBAL_ACTION_HOME
            "recents"         -> GLOBAL_ACTION_RECENTS
            "notifications"   -> GLOBAL_ACTION_NOTIFICATIONS
            "quick_settings"  -> GLOBAL_ACTION_QUICK_SETTINGS
            "power_dialog"    -> GLOBAL_ACTION_POWER_DIALOG
            "split_screen"    -> GLOBAL_ACTION_TOGGLE_SPLIT_SCREEN
            "lock_screen"     -> GLOBAL_ACTION_LOCK_SCREEN
            else -> return "error: unknown global action '$name'"
        }
        return if (performGlobalAction(action)) "ok: $name"
        else "error: performGlobalAction('$name') refused"
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
    val bounds:      Rect,
    val children:    List<AccessibilityNode>,
)
