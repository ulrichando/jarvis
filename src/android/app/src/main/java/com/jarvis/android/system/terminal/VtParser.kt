package com.jarvis.android.system.terminal

import android.util.Log
import androidx.compose.ui.graphics.Color
import com.jarvis.android.core.designsystem.JarvisPalette

/**
 * Kotlin wrapper around the native VT100/xterm-256color parser in [vt_parser.cpp].
 *
 * Each [TerminalSessionManager] session owns exactly one [VtParser] instance.
 * The native [Terminal] object (heap-allocated C++) is kept alive via [handle]
 * and freed in [close].
 *
 * Usage:
 *   val parser = VtParser(rows = 24, cols = 80)
 *   parser.feed(bytes)                  // process PTY output
 *   val grid   = parser.getGrid()       // snapshot for rendering
 *   val cursor = parser.getCursorPos()  // for cursor blink animation
 *   parser.close()                      // free native memory
 *
 * Grid byte format (from [nativeGetGrid]):
 *   Each cell = 13 bytes, row-major (top-left first):
 *     [0-3]  uint32 LE — Unicode codepoint
 *     [4-7]  uint32 LE — fg color (see [decodeColor])
 *     [8-11] uint32 LE — bg color
 *     [12]   uint8     — attr bitmask (see ATTR_* constants)
 *
 * Color encoding constants (match vt_parser.cpp):
 *   0x40000000          — terminal default fg/bg
 *   0x80000000 | index  — 256-colour palette index
 *   0x00RRGGBB          — 24-bit truecolor
 *
 * Thread safety: NOT thread-safe. Feed and render must happen on the same
 * single-thread dispatcher ([TerminalSessionManager.ptyDispatcher]).
 */
class VtParser(rows: Int, cols: Int) : AutoCloseable {

    var rows: Int = rows
        private set
    var cols: Int = cols
        private set

    private var handle: Long = nativeCreate(rows, cols)

    val isAlive: Boolean get() = handle != 0L

    // ── Feed / render ─────────────────────────────────────────────────────

    /** Feed raw PTY output bytes into the parser. */
    fun feed(bytes: ByteArray) {
        if (handle == 0L) return
        nativeFeed(handle, bytes, bytes.size)
    }

    /**
     * Returns the full visible grid as a flat [ByteArray].
     * 13 bytes per cell, [rows] × [cols] cells.
     * Returns null if the native object has been freed.
     */
    fun getGrid(): ByteArray? {
        if (handle == 0L) return null
        return nativeGetGrid(handle)
    }

    /** Returns the current cursor position (row, col), both 0-indexed. */
    fun getCursorPos(): Pair<Int, Int> =
        if (handle == 0L) 0 to 0
        else nativeGetCursorRow(handle) to nativeGetCursorCol(handle)

    /** Returns true when the cursor should be visible (DECTCEM on). */
    fun isCursorVisible(): Boolean =
        handle != 0L && nativeIsCursorVisible(handle)

    /** Returns the window title set via OSC 0/2, or empty string. */
    fun getTitle(): String =
        if (handle == 0L) "" else nativeGetTitle(handle)

    /** Number of lines in the scrollback buffer above the visible area. */
    fun scrollbackSize(): Int =
        if (handle == 0L) 0 else nativeGetScrollbackSize(handle)

    /**
     * Returns raw cell bytes for a single scrollback row.
     * [index] 0 = oldest. Returns null if out of range.
     */
    fun getScrollbackRow(index: Int): ByteArray? =
        if (handle == 0L) null else nativeGetScrollbackRow(handle, index)

    /**
     * Resize the terminal grid. Called from [TerminalSessionManager] when
     * the Composable layout changes.
     */
    fun resize(newRows: Int, newCols: Int) {
        if (handle == 0L) return
        nativeResize(handle, newRows, newCols)
        rows = newRows
        cols = newCols
    }

    /** Free the native [Terminal] object. Must be called when the session closes. */
    override fun close() {
        if (handle == 0L) return
        nativeDestroy(handle)
        handle = 0L
        Log.d(TAG, "VtParser native handle freed")
    }

    // ── JNI declarations ──────────────────────────────────────────────────

    private external fun nativeCreate(rows: Int, cols: Int): Long
    private external fun nativeFeed(handle: Long, data: ByteArray, length: Int)
    private external fun nativeGetGrid(handle: Long): ByteArray
    private external fun nativeGetCursorRow(handle: Long): Int
    private external fun nativeGetCursorCol(handle: Long): Int
    private external fun nativeIsCursorVisible(handle: Long): Boolean
    private external fun nativeGetTitle(handle: Long): String
    private external fun nativeGetScrollbackSize(handle: Long): Int
    private external fun nativeGetScrollbackRow(handle: Long, index: Int): ByteArray
    private external fun nativeResize(handle: Long, rows: Int, cols: Int)
    private external fun nativeDestroy(handle: Long)

    companion object {
        private const val TAG = "VtParser"

        // ── Attribute bitmask constants (match vt_parser.cpp) ─────────────
        const val ATTR_BOLD          = 0x01
        const val ATTR_DIM           = 0x02
        const val ATTR_ITALIC        = 0x04
        const val ATTR_UNDERLINE     = 0x08
        const val ATTR_BLINK         = 0x10
        const val ATTR_REVERSE       = 0x20
        const val ATTR_INVISIBLE     = 0x40
        const val ATTR_STRIKETHROUGH = 0x80

        // ── Color encoding constants (match vt_parser.cpp) ────────────────
        const val COLOR_DEFAULT  = 0x40000000u
        const val COLOR_INDEXED  = 0x80000000u
        const val COLOR_RGB_MASK = 0x00FFFFFFu

        /**
         * Standard xterm 256-colour palette.
         * Indices 0-15: ANSI colours; 16-231: 6×6×6 colour cube; 232-255: greyscale.
         * Default fg/bg use the JARVIS terminal palette colours.
         */
        private val PALETTE_256: IntArray by lazy { buildXterm256Palette() }

        /**
         * Resolves a packed native color value to a Compose [Color].
         *
         * @param packed   Raw uint32 from the grid byte array.
         * @param isText   True for fg (use [defaultFg]), false for bg (use [defaultBg]).
         */
        fun decodeColor(packed: UInt, isText: Boolean): Color {
            return when {
                packed == COLOR_DEFAULT -> if (isText) DefaultFg else DefaultBg
                (packed and COLOR_INDEXED) != 0u -> {
                    val index = (packed and 0xFFu).toInt().coerceIn(0, 255)
                    Color(PALETTE_256[index] or 0xFF000000.toInt())
                }
                else -> {
                    val rgb = (packed and COLOR_RGB_MASK).toInt()
                    Color(rgb or 0xFF000000.toInt())
                }
            }
        }

        /**
         * Decode a single cell from the flat grid byte array.
         *
         * @param grid  The full grid from [VtParser.getGrid].
         * @param row   0-indexed row.
         * @param col   0-indexed column.
         * @param cols  Total column count (from [VtParser.cols]).
         */
        fun decodeCell(grid: ByteArray, row: Int, col: Int, cols: Int): TerminalCell {
            val offset = (row * cols + col) * BYTES_PER_CELL
            if (offset + BYTES_PER_CELL > grid.size) return TerminalCell.SPACE

            fun readU32(i: Int): UInt =
                ((grid[i].toInt() and 0xFF) or
                 ((grid[i+1].toInt() and 0xFF) shl 8) or
                 ((grid[i+2].toInt() and 0xFF) shl 16) or
                 ((grid[i+3].toInt() and 0xFF) shl 24)).toUInt()

            val cp    = readU32(offset).toInt()
            val fgRaw = readU32(offset + 4)
            val bgRaw = readU32(offset + 8)
            val attrs = grid[offset + 12].toInt() and 0xFF

            return TerminalCell(
                codepoint = if (cp < 0x20) 0x20 else cp,
                fg        = decodeColor(fgRaw, isText = true),
                bg        = decodeColor(bgRaw, isText = false),
                bold          = (attrs and ATTR_BOLD)          != 0,
                italic        = (attrs and ATTR_ITALIC)        != 0,
                underline     = (attrs and ATTR_UNDERLINE)     != 0,
                strikethrough = (attrs and ATTR_STRIKETHROUGH) != 0,
                blink         = (attrs and ATTR_BLINK)         != 0,
                reverse       = (attrs and ATTR_REVERSE)       != 0,
                invisible     = (attrs and ATTR_INVISIBLE)     != 0,
                dim           = (attrs and ATTR_DIM)           != 0,
            )
        }

        const val BYTES_PER_CELL = 13

        val DefaultFg = JarvisPalette.TerminalText
        val DefaultBg = JarvisPalette.TerminalBg

        private fun buildXterm256Palette(): IntArray {
            val p = IntArray(256)
            // 0-7: standard ANSI
            val ansi8 = intArrayOf(
                0x000000, 0xAA0000, 0x00AA00, 0xAA5500,
                0x0000AA, 0xAA00AA, 0x00AAAA, 0xAAAAAA,
            )
            ansi8.copyInto(p)
            // 8-15: bright variants
            val bright8 = intArrayOf(
                0x555555, 0xFF5555, 0x55FF55, 0xFFFF55,
                0x5555FF, 0xFF55FF, 0x55FFFF, 0xFFFFFF,
            )
            bright8.copyInto(p, destinationOffset = 8)
            // 16-231: 6×6×6 colour cube
            val levels = intArrayOf(0, 95, 135, 175, 215, 255)
            for (i in 0..215) {
                val r = levels[i / 36]
                val g = levels[(i / 6) % 6]
                val b = levels[i % 6]
                p[16 + i] = (r shl 16) or (g shl 8) or b
            }
            // 232-255: greyscale ramp
            for (i in 0..23) {
                val v = 8 + i * 10
                p[232 + i] = (v shl 16) or (v shl 8) or v
            }
            return p
        }
    }
}

// ── Terminal cell data class ──────────────────────────────────────────────────

/**
 * Decoded representation of a single terminal cell.
 * Used by [TerminalView] to render each character.
 */
data class TerminalCell(
    val codepoint:     Int,
    val fg:            Color,
    val bg:            Color,
    val bold:          Boolean = false,
    val italic:        Boolean = false,
    val underline:     Boolean = false,
    val strikethrough: Boolean = false,
    val blink:         Boolean = false,
    val reverse:       Boolean = false,
    val invisible:     Boolean = false,
    val dim:           Boolean = false,
) {
    /** The character to draw (space if invisible or codepoint < 0x20). */
    val char: String get() = if (invisible) " " else String(Character.toChars(codepoint))

    /** Effective fg color after applying reverse-video attribute. */
    val effectiveFg: Color get() = if (reverse) bg else fg

    /** Effective bg color after applying reverse-video attribute. */
    val effectiveBg: Color get() = if (reverse) fg else bg

    companion object {
        val SPACE = TerminalCell(
            codepoint = 0x20,
            fg        = VtParser.DefaultFg,
            bg        = VtParser.DefaultBg,
        )
    }
}
