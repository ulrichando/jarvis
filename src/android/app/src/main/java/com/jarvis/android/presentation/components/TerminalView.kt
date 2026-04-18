package com.jarvis.android.presentation.components

import android.graphics.Paint
import android.graphics.Typeface
import android.view.KeyEvent
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.focusable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.input.key.onKeyEvent
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.jarvis.android.core.designsystem.JarvisPalette
import com.jarvis.android.system.terminal.TerminalGridSnapshot
import com.jarvis.android.system.terminal.VtParser
import kotlinx.coroutines.delay

/**
 * Canvas-based PTY terminal renderer.
 *
 * Reads [snapshot] (a [TerminalGridSnapshot] from [TerminalSessionManager])
 * and draws each cell character using a monospace [Paint] backed by
 * JetBrains Mono.
 *
 * ## Cell layout
 * Cell width is derived from the advance width of a single space character
 * at [FONT_SIZE_SP]. Cell height is the font's `descent - ascent`. On first
 * composition the measured dimensions are forwarded via [onResize] so the
 * PTY knows the actual grid dimensions.
 *
 * ## Input
 * A zero-size [BasicTextField] sits invisibly below the Canvas. Tapping the
 * view requests its focus; keyboard events are forwarded to [onInput].
 *
 * ## Cursor blink
 * A 500 ms coroutine toggles [cursorBlinkOn] independently of new frames so
 * the cursor blinks even when no data arrives from the shell.
 */
@Composable
fun TerminalView(
    snapshot:  TerminalGridSnapshot,
    onInput:   (String) -> Unit,
    onResize:  (rows: Int, cols: Int) -> Unit,
    modifier:  Modifier = Modifier,
) {
    val density        = LocalDensity.current
    val focusRequester = remember { FocusRequester() }
    var inputBuffer    by remember { mutableStateOf("") }
    var cursorBlinkOn  by remember { mutableStateOf(true) }

    // Cursor blink independent of data flow
    LaunchedEffect(Unit) {
        while (true) {
            delay(500)
            cursorBlinkOn = !cursorBlinkOn
        }
    }

    // Build paint objects once; rebuild if density changes
    val (textPaint, cellW, cellH, baseline) = remember(density) {
        buildPaintMetrics(density.density)
    }

    Box(
        modifier = modifier
            .background(JarvisPalette.TerminalBg)
            .pointerInput(Unit) { detectTapGestures { focusRequester.requestFocus() } }
    ) {
        // Hidden 1×1 dp input sink — captures hardware keyboard events
        BasicTextField(
            value         = inputBuffer,
            onValueChange = { new ->
                when {
                    new.length > inputBuffer.length && new.startsWith(inputBuffer) -> {
                        // Normal append — send the new characters
                        val appended = new.substring(inputBuffer.length)
                            .replace("\n", "\r")   // IME Enter → carriage return
                        if (appended.isNotEmpty()) onInput(appended)
                    }
                    new.length < inputBuffer.length -> {
                        // Deletion — send DEL
                        onInput("\u007F")
                    }
                    new != inputBuffer -> {
                        // Full replacement (autocorrect / paste / composition commit)
                        val replaced = new.replace("\n", "\r")
                        if (replaced.isNotEmpty()) onInput(replaced)
                    }
                }
                // Keep buffer capped to avoid memory bloat from long pastes
                inputBuffer = new.takeLast(64)
            },
            modifier = Modifier
                .size(1.dp)
                .alpha(0f)
                .focusRequester(focusRequester)
                .focusable()
                .onKeyEvent { keyEvent ->
                    val native = keyEvent.nativeKeyEvent
                    if (native.action == KeyEvent.ACTION_DOWN) {
                        val isCtrl = native.isCtrlPressed
                        val seq = if (isCtrl) ctrlKeyToAnsi(native.keyCode)
                                  else keyEventToAnsi(native.keyCode)
                        if (seq != null) { onInput(seq); true } else false
                    } else false
                },
        )

        Canvas(
            modifier = Modifier
                .fillMaxSize()
                .onSizeChanged { size ->
                    if (cellW > 0f && cellH > 0f) {
                        val cols = (size.width  / cellW).toInt().coerceAtLeast(1)
                        val rows = (size.height / cellH).toInt().coerceAtLeast(1)
                        onResize(rows, cols)
                    }
                },
        ) {
            drawTerminal(
                snapshot      = snapshot,
                textPaint     = textPaint,
                cellW         = cellW,
                cellH         = cellH,
                baseline      = baseline,
                cursorBlinkOn = cursorBlinkOn && snapshot.cursorVisible,
            )
        }
    }
}

// ── Drawing ───────────────────────────────────────────────────────────────────

private fun DrawScope.drawTerminal(
    snapshot:      TerminalGridSnapshot,
    textPaint:     Paint,
    cellW:         Float,
    cellH:         Float,
    baseline:      Float,
    cursorBlinkOn: Boolean,
) {
    val grid = snapshot.grid
    if (grid.isEmpty()) return

    val rows = snapshot.rows
    val cols = snapshot.cols

    for (row in 0 until rows) {
        for (col in 0 until cols) {
            val cell  = VtParser.decodeCell(grid, row, col, cols)
            val left  = col * cellW
            val top   = row * cellH

            // Background
            val isAtCursor = cursorBlinkOn &&
                             row == snapshot.cursorRow &&
                             col == snapshot.cursorCol

            val bgColor = if (isAtCursor) Color.White else cell.effectiveBg
            drawRect(
                color    = bgColor,
                topLeft  = Offset(left, top),
                size     = Size(cellW, cellH),
            )

            // Character
            val charStr = cell.char
            if (charStr.isNotBlank() && charStr != " ") {
                val fgColor = if (isAtCursor) Color.Black else cell.effectiveFg
                textPaint.color = fgColor.toArgb()
                textPaint.isFakeBoldText = cell.bold

                drawIntoCanvas { canvas ->
                    canvas.nativeCanvas.drawText(
                        charStr,
                        left + cellW * 0.5f,
                        top  + baseline,
                        textPaint,
                    )
                }
            }

            // Underline decoration
            if (cell.underline) {
                drawRect(
                    color   = cell.effectiveFg,
                    topLeft = Offset(left, top + cellH - 1.5f),
                    size    = Size(cellW, 1.5f),
                )
            }

            // Strikethrough decoration
            if (cell.strikethrough) {
                drawRect(
                    color   = cell.effectiveFg,
                    topLeft = Offset(left, top + cellH * 0.5f),
                    size    = Size(cellW, 1.5f),
                )
            }
        }
    }
}

// ── Paint / metrics ───────────────────────────────────────────────────────────

private data class PaintMetrics(
    val paint:    Paint,
    val cellW:    Float,
    val cellH:    Float,
    val baseline: Float,
)

private fun buildPaintMetrics(density: Float): PaintMetrics {
    val paint = Paint().apply {
        isAntiAlias = true
        typeface    = Typeface.MONOSPACE
        textSize    = FONT_SIZE_SP * density
        textAlign   = Paint.Align.CENTER
    }
    val fm       = paint.fontMetrics
    val cellH    = (-fm.ascent + fm.descent) * 1.2f    // add 20% line spacing
    val baseline = -fm.ascent + (cellH - (-fm.ascent + fm.descent)) * 0.5f
    val cellW    = paint.measureText("M")               // monospace: any char = same width
    return PaintMetrics(paint, cellW, cellH, baseline)
}

// ── Key mapping ───────────────────────────────────────────────────────────────

private fun keyEventToAnsi(keyCode: Int): String? = when (keyCode) {
    KeyEvent.KEYCODE_ENTER       -> "\r"
    KeyEvent.KEYCODE_NUMPAD_ENTER -> "\r"
    KeyEvent.KEYCODE_DPAD_UP     -> "\u001B[A"
    KeyEvent.KEYCODE_DPAD_DOWN   -> "\u001B[B"
    KeyEvent.KEYCODE_DPAD_RIGHT  -> "\u001B[C"
    KeyEvent.KEYCODE_DPAD_LEFT   -> "\u001B[D"
    KeyEvent.KEYCODE_MOVE_HOME   -> "\u001B[H"
    KeyEvent.KEYCODE_MOVE_END    -> "\u001B[F"
    KeyEvent.KEYCODE_PAGE_UP     -> "\u001B[5~"
    KeyEvent.KEYCODE_PAGE_DOWN   -> "\u001B[6~"
    KeyEvent.KEYCODE_DEL         -> "\u007F"
    KeyEvent.KEYCODE_FORWARD_DEL -> "\u001B[3~"
    KeyEvent.KEYCODE_TAB         -> "\t"
    KeyEvent.KEYCODE_ESCAPE      -> "\u001B"
    else -> null
}

/** Ctrl+key → control character sequence. */
private fun ctrlKeyToAnsi(keyCode: Int): String? = when (keyCode) {
    KeyEvent.KEYCODE_A -> "\u0001"   // Ctrl+A — move to line start
    KeyEvent.KEYCODE_B -> "\u0002"   // Ctrl+B — move back one char
    KeyEvent.KEYCODE_C -> "\u0003"   // Ctrl+C — SIGINT
    KeyEvent.KEYCODE_D -> "\u0004"   // Ctrl+D — EOF / logout
    KeyEvent.KEYCODE_E -> "\u0005"   // Ctrl+E — move to line end
    KeyEvent.KEYCODE_F -> "\u0006"   // Ctrl+F — move forward one char
    KeyEvent.KEYCODE_G -> "\u0007"   // Ctrl+G — bell / cancel search
    KeyEvent.KEYCODE_K -> "\u000B"   // Ctrl+K — kill to end of line
    KeyEvent.KEYCODE_L -> "\u000C"   // Ctrl+L — clear screen
    KeyEvent.KEYCODE_N -> "\u000E"   // Ctrl+N — next history
    KeyEvent.KEYCODE_P -> "\u0010"   // Ctrl+P — previous history
    KeyEvent.KEYCODE_R -> "\u0012"   // Ctrl+R — reverse history search
    KeyEvent.KEYCODE_U -> "\u0015"   // Ctrl+U — kill to start of line
    KeyEvent.KEYCODE_W -> "\u0017"   // Ctrl+W — kill previous word
    KeyEvent.KEYCODE_Z -> "\u001A"   // Ctrl+Z — SIGTSTP
    KeyEvent.KEYCODE_BACKSLASH -> "\u001C"  // Ctrl+\ — SIGQUIT
    else -> null
}

private const val FONT_SIZE_SP = 12f
