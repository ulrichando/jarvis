package com.jarvis.android.presentation.components

import android.graphics.Paint
import android.graphics.Typeface
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import com.jarvis.android.core.designsystem.JarvisPalette
import com.jarvis.android.system.terminal.TerminalCell
import com.jarvis.android.system.terminal.TerminalGridSnapshot
import com.jarvis.android.system.terminal.VtParser
import kotlinx.coroutines.delay

/**
 * Composable terminal view backed by a native [TerminalInputSink] for IME
 * input and a Compose [Canvas] for drawing. Laid out as:
 *
 *   Box
 *     ├── Canvas           — renders the terminal grid (chars, cursor)
 *     └── TerminalInputSink — transparent Android View that owns the IME
 *                              connection; tapping anywhere sends focus to
 *                              it and opens the soft keyboard.
 *
 * The sink's [TerminalInputSink.onInput] forwards every typed character
 * straight to the PTY through the [onInput] callback — no document, no
 * local text state.
 */
@Composable
fun TerminalView(
    snapshot:          TerminalGridSnapshot,
    onInput:           (String) -> Unit,
    onResize:          (rows: Int, cols: Int) -> Unit,
    onFetchScrollback: (Int) -> ByteArray? = { null },
    modifier:          Modifier = Modifier,
) {
    val density       = LocalDensity.current
    var cursorBlinkOn by remember { mutableStateOf(true) }

    LaunchedEffect(Unit) {
        while (true) { delay(500); cursorBlinkOn = !cursorBlinkOn }
    }

    val paints = remember(density) { buildPaintMetrics(density.density) }

    // How many rows up the user has scrolled from the bottom. 0 = live view
    // (bottom), max = snapshot.scrollbackSize (oldest row at top of viewport).
    var scrollOffset by remember { mutableStateOf(0) }
    // Pixels accumulator between row-sized steps — the gesture fires at the
    // Android View level with continuous deltas; we convert to integer rows.
    var dragAccumPx  by remember { mutableStateOf(0f) }

    // New live data arriving while we're scrolled back: stay pinned to the
    // user's current position instead of snapping to the bottom.
    // (We only reset to 0 on explicit input below.)

    Box(modifier = modifier.background(JarvisPalette.TerminalBg)) {
        Canvas(
            modifier = Modifier
                .fillMaxSize()
                .onSizeChanged { size ->
                    if (paints.cellW > 0f && paints.cellH > 0f) {
                        val cols = (size.width  / paints.cellW).toInt().coerceAtLeast(1)
                        val rows = (size.height / paints.cellH).toInt().coerceAtLeast(1)
                        onResize(rows, cols)
                    }
                },
        ) {
            drawTerminal(
                snapshot          = snapshot,
                paints            = paints,
                cursorBlinkOn     = cursorBlinkOn && snapshot.cursorVisible && scrollOffset == 0,
                scrollOffset      = scrollOffset.coerceIn(0, snapshot.scrollbackSize),
                onFetchScrollback = onFetchScrollback,
            )
        }

        // Transparent input-capture layer. Sits on top of the Canvas so taps
        // land here, which requests focus and opens the IME. Typing flows
        // through its InputConnection straight to onInput → PTY. Vertical
        // drags are captured separately and translated into scrollback rows.
        AndroidView(
            modifier = Modifier.fillMaxSize(),
            factory  = { ctx ->
                TerminalInputSink(ctx).apply {
                    this.onInput = { text ->
                        // Typing always snaps back to the live bottom — matches
                        // every standard terminal emulator.
                        scrollOffset = 0
                        onInput(text)
                    }
                    this.onVerticalDrag = { dy ->
                        val rowStep = paints.cellH
                        if (rowStep > 0f) {
                            dragAccumPx += dy
                            val steps = (dragAccumPx / rowStep).toInt()
                            if (steps != 0) {
                                dragAccumPx -= steps * rowStep
                                // Finger down (positive dy) = pull newer
                                // content down = decrease offset (toward
                                // bottom). Finger up (negative dy) = reveal
                                // older content = increase offset.
                                scrollOffset = (scrollOffset - steps)
                                    .coerceIn(0, snapshot.scrollbackSize)
                            }
                        }
                    }
                    post {
                        requestFocus()
                        showKeyboard()
                    }
                }
            },
            update = { view ->
                view.onInput = { text ->
                    scrollOffset = 0
                    onInput(text)
                }
            },
        )

        // Small top-right indicator while scrolled back so the user knows
        // they're looking at history, not the live shell.
        if (scrollOffset > 0) {
            Box(
                modifier = Modifier
                    .align(Alignment.TopEnd)
                    .padding(8.dp),
            ) {
                Text(
                    text  = "↑ -$scrollOffset",
                    color = Color(0xFFFFCC00),
                    style = MaterialTheme.typography.labelSmall,
                )
            }
        }
    }
}

// ── Drawing ───────────────────────────────────────────────────────────────────

private fun DrawScope.drawTerminal(
    snapshot:          TerminalGridSnapshot,
    paints:            PaintMetrics,
    cursorBlinkOn:     Boolean,
    scrollOffset:      Int,
    onFetchScrollback: (Int) -> ByteArray?,
) {
    val grid = snapshot.grid
    val cols = snapshot.cols
    val rows = snapshot.rows
    if (grid.isEmpty() || cols <= 0 || rows <= 0) return

    val canvas   = drawContext.canvas.nativeCanvas
    val cellW    = paints.cellW
    val cellH    = paints.cellH
    val baseline = paints.baseline
    val bgPaint  = paints.bgPaint
    val fgPaint  = paints.fgPaint
    val defaultBgArgb = JarvisPalette.TerminalBg.toArgb()

    val sb       = snapshot.scrollbackSize
    // Logical row = (SB - scrollOffset) + row_in_view
    //   < SB → scrollback row (fetch via callback)
    //   >= SB → visible grid row (total - SB)
    val baseLogical = sb - scrollOffset

    val buf = StringBuilder(cols)

    for (row in 0 until rows) {
        val y = row * cellH
        val logical = baseLogical + row

        // Pick the source ByteArray + row index for this viewport row.
        val (sourceGrid, sourceRow) =
            if (logical < sb) (onFetchScrollback(logical) to 0)
            else              (grid to (logical - sb))

        // If the scrollback fetch missed (old row recycled), skip.
        if (sourceGrid == null || sourceGrid.isEmpty()) continue

        // ── Pass 1: background runs ───────────────────────────────────────
        var runStart = 0
        var runArgb  = defaultBgArgb
        var col      = 0
        while (col < cols) {
            val cell = VtParser.decodeCell(sourceGrid, sourceRow, col, cols)
            val argb = cell.effectiveBg.toArgb()
            if (col == 0) { runStart = 0; runArgb = argb }
            else if (argb != runArgb) {
                flushBgRun(canvas, bgPaint, runArgb, defaultBgArgb,
                           runStart, col, y, cellW, cellH)
                runStart = col
                runArgb  = argb
            }
            col++
        }
        flushBgRun(canvas, bgPaint, runArgb, defaultBgArgb,
                   runStart, cols, y, cellW, cellH)

        // ── Pass 2: text runs ─────────────────────────────────────────────
        col = 0
        var tRunStart = 0
        var tRunFg    = 0
        var tRunBold  = false
        var tRunItalic = false
        var tRunUnder = false
        var tRunDim   = false
        buf.setLength(0)

        while (col < cols) {
            val cell = VtParser.decodeCell(sourceGrid, sourceRow, col, cols)
            val cp   = cell.codepoint
            val ch   = if (cell.invisible || cp < 0x20) ' '
                       else Character.toChars(cp).concatToString().firstOrNull() ?: ' '

            val fgArgb = cell.effectiveFg.toArgb()
            if (col == 0) {
                tRunStart  = 0
                tRunFg     = fgArgb
                tRunBold   = cell.bold
                tRunItalic = cell.italic
                tRunUnder  = cell.underline
                tRunDim    = cell.dim
                buf.append(ch)
            } else if (fgArgb != tRunFg || cell.bold != tRunBold ||
                       cell.italic != tRunItalic || cell.underline != tRunUnder ||
                       cell.dim != tRunDim) {
                flushTextRun(canvas, fgPaint, buf.toString(), tRunFg,
                             tRunBold, tRunItalic, tRunUnder, tRunDim,
                             tRunStart, y, cellW, baseline)
                buf.setLength(0)
                tRunStart  = col
                tRunFg     = fgArgb
                tRunBold   = cell.bold
                tRunItalic = cell.italic
                tRunUnder  = cell.underline
                tRunDim    = cell.dim
                buf.append(ch)
            } else {
                buf.append(ch)
            }
            col++
        }
        flushTextRun(canvas, fgPaint, buf.toString(), tRunFg,
                     tRunBold, tRunItalic, tRunUnder, tRunDim,
                     tRunStart, y, cellW, baseline)
    }

    if (cursorBlinkOn) {
        val cursorX = snapshot.cursorCol * cellW
        val cursorY = snapshot.cursorRow * cellH
        drawRect(
            color   = Color(0xFFFFCC00),
            topLeft = Offset(cursorX, cursorY + cellH * 0.85f),
            size    = Size(cellW, cellH * 0.15f),
        )
    }
}

private fun flushBgRun(
    canvas: android.graphics.Canvas,
    paint:  Paint,
    argb:   Int,
    defaultArgb: Int,
    startCol: Int,
    endCol:   Int,
    y:        Float,
    cellW:    Float,
    cellH:    Float,
) {
    if (argb == defaultArgb || startCol >= endCol) return
    paint.color = argb
    canvas.drawRect(
        startCol * cellW,
        y,
        endCol * cellW,
        y + cellH,
        paint,
    )
}

private fun flushTextRun(
    canvas: android.graphics.Canvas,
    paint:  Paint,
    text:   String,
    fgArgb: Int,
    bold:    Boolean,
    italic:  Boolean,
    under:   Boolean,
    dim:     Boolean,
    startCol: Int,
    y:        Float,
    cellW:    Float,
    baseline: Float,
) {
    if (text.isEmpty()) return
    if (text.all { it == ' ' } && !under) return  // nothing visible
    // Dim fades the fg 50% toward black (termux pattern)
    paint.color = if (dim) dimArgb(fgArgb) else fgArgb
    val style = when {
        bold && italic -> Typeface.BOLD_ITALIC
        bold           -> Typeface.BOLD
        italic         -> Typeface.ITALIC
        else           -> Typeface.NORMAL
    }
    if (paint.typeface.style != style) {
        paint.typeface = Typeface.create(Typeface.MONOSPACE, style)
    }
    val textX = startCol * cellW
    canvas.drawText(text, textX, y + baseline, paint)
    if (under) {
        val uy = y + baseline + 2f
        canvas.drawLine(textX, uy, textX + text.length * cellW, uy, paint)
    }
}

private fun dimArgb(argb: Int): Int {
    val a = (argb ushr 24) and 0xFF
    val r = ((argb ushr 16) and 0xFF) / 2
    val g = ((argb ushr 8)  and 0xFF) / 2
    val b = (argb and 0xFF) / 2
    return (a shl 24) or (r shl 16) or (g shl 8) or b
}

private data class PaintMetrics(
    val fgPaint:  Paint,
    val bgPaint:  Paint,
    val cellW:    Float,
    val cellH:    Float,
    val baseline: Float,
)

private fun buildPaintMetrics(densityFloat: Float): PaintMetrics {
    val fg = Paint().apply {
        isAntiAlias = true
        color       = Color(0xFFECECEC).toArgb()
        textSize    = 13f * densityFloat
        typeface    = Typeface.MONOSPACE
    }
    val bg = Paint().apply {
        isAntiAlias = false
        style       = Paint.Style.FILL
    }
    val fm       = fg.fontMetrics
    val cellH    = (fm.descent - fm.ascent) + 2f
    val cellW    = fg.measureText("M")
    val baseline = -fm.ascent + 1f
    return PaintMetrics(fg, bg, cellW, cellH, baseline)
}
