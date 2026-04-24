package com.jarvis.android.presentation.components

import android.content.Context
import android.graphics.Canvas
import android.graphics.Rect
import android.text.InputType
import android.util.Log
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import android.view.inputmethod.BaseInputConnection
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputConnection
import android.view.inputmethod.InputMethodManager

private const val TAG = "TerminalInputSink"

/**
 * Android View that bridges the soft keyboard (or a hardware keyboard) to a
 * raw byte-stream callback, so typed characters can be written straight into
 * a PTY. This is exactly the pattern Termux, ConnectBot, and Jack Palevich's
 * Android Terminal Emulator use — an invisible-but-focusable View with a
 * custom [InputConnection] that intercepts IME events and forwards them
 * as bytes instead of managing a text model.
 *
 * Why not a `BasicTextField`:
 *   - Compose text fields maintain an internal text model. A terminal has no
 *     "current text" — every keystroke is a one-way write to the shell.
 *   - Hidden / zero-alpha TextFields don't reliably bind to Android IMEs on
 *     OneUI / Samsung keyboards; the soft keyboard often refuses to open
 *     against them, which is the "can't type in the terminal" bug.
 *
 * Usage: wrap in Compose with `AndroidView(factory = { TerminalInputSink(it) })`
 * and set [onInput] before anything else. Call [showKeyboard] after focus to
 * pop the soft keyboard. See [TerminalView] for the composition.
 */
class TerminalInputSink @JvmOverloads constructor(
    context: Context,
    attrs:   android.util.AttributeSet? = null,
) : View(context, attrs) {

    /** Stream of bytes typed by the user. UTF-8 string chunks. */
    var onInput: (String) -> Unit = {}

    /**
     * Vertical scroll gesture, in pixels. Positive = finger moved down
     * (user pulling history forward), negative = finger moved up (user
     * pulling older history into view). [TerminalView] converts pixels
     * into row deltas against its scrollback buffer.
     */
    var onVerticalDrag: (Float) -> Unit = {}

    private var dragLastY:    Float   = 0f
    private var dragTotalAbs: Float   = 0f
    private var isDragging:   Boolean = false
    private val dragSlopPx:   Float   = context.resources.displayMetrics.density * 8f

    init {
        isFocusable            = true
        isFocusableInTouchMode = true
        isClickable            = true      // Termux has this; without it some ROMs swallow taps
        isLongClickable        = true
        setBackgroundColor(0x00000000)     // fully transparent
    }

    // Android treats a View as a text editor only if this returns true.
    override fun onCheckIsTextEditor(): Boolean = true

    override fun onCreateInputConnection(outAttrs: EditorInfo): InputConnection {
        Log.i(TAG, "onCreateInputConnection called — IME is binding to the sink")
        outAttrs.inputType  = InputType.TYPE_CLASS_TEXT or
                              InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD or
                              InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
        outAttrs.imeOptions = EditorInfo.IME_FLAG_NO_EXTRACT_UI or
                              EditorInfo.IME_FLAG_NO_FULLSCREEN or
                              EditorInfo.IME_FLAG_NO_PERSONALIZED_LEARNING or
                              EditorInfo.IME_ACTION_NONE
        return TerminalInputConnection(this, onInput)
    }

    // Pulling up the soft keyboard requires (a) focus and (b) a request to
    // the InputMethodManager. Calling both on every tap handles the common
    // "I tapped the terminal but nothing happened" case cleanly.
    override fun onTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                dragLastY    = event.y
                dragTotalAbs = 0f
                isDragging   = false
            }
            MotionEvent.ACTION_MOVE -> {
                val dy = event.y - dragLastY
                dragLastY = event.y
                dragTotalAbs += kotlin.math.abs(dy)
                if (!isDragging && dragTotalAbs > dragSlopPx) isDragging = true
                if (isDragging) onVerticalDrag(dy)
            }
            MotionEvent.ACTION_UP -> {
                // A pure tap (no drag) re-focuses the sink and opens the IME.
                // A drag scrolls instead of poking the keyboard.
                if (!isDragging) {
                    requestFocus()
                    showKeyboard()
                }
                isDragging = false
            }
            MotionEvent.ACTION_CANCEL -> {
                isDragging = false
            }
        }
        return true
    }

    fun showKeyboard() {
        val imm = context.getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager?
            ?: return
        // Two attempts: the first usually works but can silently fail on
        // Samsung/OneUI when the View hasn't been fully attached yet. A
        // 50-ms post-delayed retry covers that race, and a FORCED flag on
        // the retry tells the system we really, really want the keyboard.
        imm.showSoftInput(this, InputMethodManager.SHOW_IMPLICIT)
        postDelayed({
            if (hasWindowFocus() && isFocused) {
                imm.showSoftInput(this, InputMethodManager.SHOW_FORCED)
            }
        }, 80)
    }

    // Hardware keyboard / special-key path. All soft-keyboard input goes
    // through InputConnection.commitText; this path catches arrows, ctrl,
    // function keys etc. when a physical keyboard is attached.
    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        val seq = keyEventToBytes(keyCode, event) ?: return super.onKeyDown(keyCode, event)
        onInput(seq)
        return true
    }

    // Visual focus ring — terminal should look "active" when it has focus so
    // the user knows their keyboard input will land.
    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
    }

    private fun keyEventToBytes(keyCode: Int, event: KeyEvent): String? {
        val ctrl = event.isCtrlPressed
        return when {
            ctrl && keyCode in KeyEvent.KEYCODE_A..KeyEvent.KEYCODE_Z -> {
                val code = keyCode - KeyEvent.KEYCODE_A + 1
                Char(code).toString()                // Ctrl-A ..  Ctrl-Z
            }
            keyCode == KeyEvent.KEYCODE_ENTER       -> "\r"
            keyCode == KeyEvent.KEYCODE_TAB         -> "\t"
            keyCode == KeyEvent.KEYCODE_ESCAPE      -> ""
            keyCode == KeyEvent.KEYCODE_DEL         -> ""
            keyCode == KeyEvent.KEYCODE_DPAD_UP     -> "[A"
            keyCode == KeyEvent.KEYCODE_DPAD_DOWN   -> "[B"
            keyCode == KeyEvent.KEYCODE_DPAD_RIGHT  -> "[C"
            keyCode == KeyEvent.KEYCODE_DPAD_LEFT   -> "[D"
            keyCode == KeyEvent.KEYCODE_MOVE_HOME   -> "[H"
            keyCode == KeyEvent.KEYCODE_MOVE_END    -> "[F"
            keyCode == KeyEvent.KEYCODE_PAGE_UP     -> "[5~"
            keyCode == KeyEvent.KEYCODE_PAGE_DOWN   -> "[6~"
            else -> {
                val u = event.unicodeChar
                if (u == 0) null else Char(u).toString()
            }
        }
    }
}

/**
 * Custom InputConnection that forwards IME text events (commitText, composing
 * text, deletions) straight to [onInput] instead of maintaining an editable
 * text buffer. This is the same shape Termux's `TerminalInputConnection`
 * uses — treat the view as a byte pipe, not a document.
 */
private class TerminalInputConnection(
    target:   View,
    private val onInput: (String) -> Unit,
) : BaseInputConnection(target, true) {

    /**
     * Snapshot of the text currently in the IME's composition buffer. Every
     * setComposingText call replaces this — we diff against the previous
     * snapshot to forward ONLY the new/deleted characters to the PTY,
     * instead of resending the full composition each time (which flooded
     * the shell with "h he hel hell hello" for a typed "hello").
     */
    private var composing: String = ""

    override fun commitText(text: CharSequence?, newCursorPosition: Int): Boolean {
        val s = text?.toString().orEmpty()
        val diff = diffOut(composing, s)
        Log.i(TAG, "commitText old='$composing' new='$s' diff='$diff'")
        composing = ""
        if (diff.isNotEmpty()) onInput(diff.replace("\n", "\r"))
        return true
    }

    override fun setComposingText(text: CharSequence?, newCursorPosition: Int): Boolean {
        val s    = text?.toString().orEmpty()
        val diff = diffOut(composing, s)
        Log.i(TAG, "setComposingText old='$composing' new='$s' diff='$diff'")
        composing = s
        if (diff.isNotEmpty()) onInput(diff.replace("\n", "\r"))
        return true
    }

    override fun finishComposingText(): Boolean {
        composing = ""
        return true
    }

    /**
     * Compute the characters the IME added or removed between two composition
     * snapshots. If `new` starts with `old` it's a pure append — return the
     * suffix. If the composition shortened or changed, return DELs for the
     * removed chars plus the new suffix.
     */
    private fun diffOut(old: String, new: String): String {
        var i = 0
        val max = minOf(old.length, new.length)
        while (i < max && old[i] == new[i]) i++
        val dels  = old.length - i
        val adds  = new.substring(i)
        val sb    = StringBuilder()
        repeat(dels) { sb.append('') }
        sb.append(adds)
        return sb.toString()
    }

    override fun deleteSurroundingText(beforeLength: Int, afterLength: Int): Boolean {
        // Backspace events often arrive this way rather than via sendKeyEvent.
        // Send one DEL per char the IME wanted to remove.
        repeat(beforeLength) { onInput("") }
        return true
    }

    override fun sendKeyEvent(event: KeyEvent): Boolean {
        if (event.action != KeyEvent.ACTION_DOWN) return true
        when (event.keyCode) {
            KeyEvent.KEYCODE_ENTER -> onInput("\r")
            KeyEvent.KEYCODE_DEL   -> onInput("")
            KeyEvent.KEYCODE_TAB   -> onInput("\t")
            else -> {
                val u = event.unicodeChar
                if (u != 0) onInput(Char(u).toString())
            }
        }
        return true
    }

    // Allow text-selection / composing operations to succeed without crashing
    // — we don't actually maintain a document, so these are no-ops that return
    // success.
    override fun getTextBeforeCursor(length: Int, flags: Int): CharSequence = ""
    override fun getTextAfterCursor(length: Int, flags: Int): CharSequence  = ""
    override fun getSelectedText(flags: Int): CharSequence?                 = null
    override fun beginBatchEdit(): Boolean = true
    override fun endBatchEdit():   Boolean = true

    private companion object {
        @Suppress("unused")
        private val EMPTY_RECT = Rect()
    }
}
