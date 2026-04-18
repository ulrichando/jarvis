/**
 * vt_parser.cpp — VT100/xterm-256color escape sequence parser + terminal grid.
 *
 * Implements the Paul Williams DEC-compatible state machine
 * (https://vt100.net/emu/dec_ansi_parser) with the following capabilities:
 *
 *   • Cursor movement  — CUP, CUF, CUB, CUU, CUD, CHA, VPA, HVP
 *   • Screen erase     — ED (0/1/2/3), EL (0/1/2)
 *   • Line ops         — IL, DL, SU, SD
 *   • Char ops         — ICH, DCH, ECH
 *   • SGR              — bold, dim, italic, underline, blink, reverse,
 *                        invisible, strikethrough; 16/256/truecolor fg+bg
 *   • Scroll region    — DECSTBM
 *   • Save/restore     — DECSC/DECRC (ESC 7/8) + SCOSC/SCORC (CSI s/u)
 *   • Alt screen       — ?47 / ?1047 / ?1049
 *   • Cursor visible   — ?25h / ?25l
 *   • Auto-wrap        — ?7h / ?7l
 *   • OSC 0/2          — window title (stored, surfaced to Kotlin)
 *   • UTF-8 decoding   — multi-byte input handled transparently
 *   • Scrollback       — ring buffer, up to MAX_SCROLLBACK_LINES (10 000)
 *
 * Grid serialisation (nativeGetGrid):
 *   Each cell = 13 bytes, row-major order:
 *     [0-3]  uint32 LE  — Unicode codepoint (0x20 = space)
 *     [4-7]  uint32 LE  — fg color  (see COLOR_* constants)
 *     [8-11] uint32 LE  — bg color
 *     [12]   uint8       — attr bitmask (ATTR_* constants)
 *
 *   Color encoding:
 *     0x40000000          — terminal default color
 *     0x80000000 | index  — 256-color palette index (0–255)
 *     0x00RRGGBB          — 24-bit truecolor
 *
 * Kotlin-facing class: com.jarvis.android.system.terminal.VtParser
 */

#include <jni.h>
#include <android/log.h>

#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <cassert>
#include <vector>
#include <deque>
#include <string>
#include <algorithm>
#include <memory>

// ── Logging ───────────────────────────────────────────────────────────────────
#define LOG_TAG "JarvisVT"
#define LOGD(...) __android_log_print(ANDROID_LOG_DEBUG, LOG_TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)

// ── Constants ─────────────────────────────────────────────────────────────────

constexpr uint32_t COLOR_DEFAULT  = 0x40000000u; // sentinel: use terminal default
constexpr uint32_t COLOR_INDEXED  = 0x80000000u; // bits 0-7 = palette index
constexpr uint32_t COLOR_RGB_MASK = 0x00FFFFFFu; // bits 0-23 = 24-bit RGB

constexpr uint8_t ATTR_BOLD          = 0x01u;
constexpr uint8_t ATTR_DIM           = 0x02u;
constexpr uint8_t ATTR_ITALIC        = 0x04u;
constexpr uint8_t ATTR_UNDERLINE     = 0x08u;
constexpr uint8_t ATTR_BLINK        = 0x10u;
constexpr uint8_t ATTR_REVERSE       = 0x20u;
constexpr uint8_t ATTR_INVISIBLE     = 0x40u;
constexpr uint8_t ATTR_STRIKETHROUGH = 0x80u;

constexpr int MAX_SCROLLBACK_LINES = 10000;
constexpr int MAX_CSI_PARAMS       = 16;
constexpr int MAX_OSC_LEN          = 1024;
constexpr int BYTES_PER_CELL       = 13;

// ── Cell ──────────────────────────────────────────────────────────────────────

struct Cell {
    uint32_t cp    = 0x20u;          // Unicode codepoint (space)
    uint32_t fg    = COLOR_DEFAULT;
    uint32_t bg    = COLOR_DEFAULT;
    uint8_t  attrs = 0;
};

using Row = std::vector<Cell>;

// ── SGR attribute accumulator ─────────────────────────────────────────────────

struct Pen {
    uint32_t fg    = COLOR_DEFAULT;
    uint32_t bg    = COLOR_DEFAULT;
    uint8_t  attrs = 0;
};

// ── Saved cursor state ────────────────────────────────────────────────────────

struct CursorState {
    int row = 0;
    int col = 0;
    Pen pen;
};

// ── Parser states (Paul Williams DEC-compatible) ──────────────────────────────

enum class State : uint8_t {
    Ground,
    Escape,
    EscapeIntermediate,
    CsiEntry,
    CsiParam,
    CsiIntermediate,
    CsiIgnore,
    OscString,
    DcsIgnore,          // DCS pass-through not needed; just ignore
    SosPmApcString,     // ignore SOS/PM/APC
    Utf8Continuation,   // collecting multi-byte UTF-8 sequence
};

// ── Terminal ───────────────────────────────────────────────────────────────────

struct Terminal {
    // ── Grid ──────────────────────────────────────────────────────────────
    int rows = 24;
    int cols = 80;

    std::vector<Row> grid;          // visible screen [0..rows-1]
    std::vector<Row> alt_grid;      // alternate screen buffer
    std::deque<Row>  scrollback;    // above visible top; oldest = front

    // ── Cursor ────────────────────────────────────────────────────────────
    int          cursor_row     = 0;
    int          cursor_col     = 0;
    bool         cursor_visible = true;
    bool         autowrap       = true;
    bool         pending_wrap   = false; // deferred wrap after last column
    CursorState  saved;
    bool         alt_screen     = false;

    // ── Scroll region ─────────────────────────────────────────────────────
    int scroll_top    = 0;       // inclusive
    int scroll_bottom = 23;      // inclusive (= rows-1)

    // ── Current pen ───────────────────────────────────────────────────────
    Pen pen;

    // ── Parser state machine ──────────────────────────────────────────────
    State   state    = State::Ground;
    int     csi_params[MAX_CSI_PARAMS]{};
    int     csi_param_count = 0;
    char    csi_inter[4]{};          // CSI intermediate bytes
    int     csi_inter_len = 0;
    bool    csi_priv = false;        // '?' prefix

    char    osc_buf[MAX_OSC_LEN]{};
    int     osc_len = 0;

    std::string title;               // window title from OSC 0/2

    // ── UTF-8 decoder ─────────────────────────────────────────────────────
    uint32_t utf8_codepoint  = 0;
    int      utf8_remaining  = 0;

    // ── Constructor ───────────────────────────────────────────────────────
    explicit Terminal(int r, int c) : rows(r), cols(c) {
        grid.resize(static_cast<size_t>(rows), Row(static_cast<size_t>(cols)));
        scroll_bottom = rows - 1;
    }

    // ── Grid helpers ──────────────────────────────────────────────────────

    Row make_blank_row() const {
        Row r(static_cast<size_t>(cols));
        for (auto& cell : r) {
            cell.fg    = pen.fg;
            cell.bg    = pen.bg;
            cell.attrs = 0;
        }
        return r;
    }

    Cell blank_cell() const {
        return Cell{ 0x20u, pen.fg, pen.bg, 0 };
    }

    void clamp_cursor() {
        cursor_row = std::clamp(cursor_row, 0, rows - 1);
        cursor_col = std::clamp(cursor_col, 0, cols - 1);
    }

    Cell& at(int r, int c) {
        return grid[static_cast<size_t>(r)][static_cast<size_t>(c)];
    }

    // ── Scrollback push ───────────────────────────────────────────────────

    void push_to_scrollback(Row&& row) {
        scrollback.push_back(std::move(row));
        if (static_cast<int>(scrollback.size()) > MAX_SCROLLBACK_LINES) {
            scrollback.pop_front();
        }
    }

    // ── Scroll up (lines scroll off top into scrollback) ──────────────────
    // Scrolls the region [scroll_top .. scroll_bottom] up by `n` lines.
    void scroll_up(int n) {
        n = std::clamp(n, 1, scroll_bottom - scroll_top + 1);
        for (int i = 0; i < n; ++i) {
            if (scroll_top == 0) {
                push_to_scrollback(std::move(grid[0]));
                grid.erase(grid.begin());
            } else {
                grid.erase(grid.begin() + scroll_top);
            }
            grid.insert(grid.begin() + scroll_bottom, make_blank_row());
        }
    }

    // ── Scroll down (insert blank lines at top of region) ─────────────────
    void scroll_down(int n) {
        n = std::clamp(n, 1, scroll_bottom - scroll_top + 1);
        for (int i = 0; i < n; ++i) {
            grid.erase(grid.begin() + scroll_bottom);
            grid.insert(grid.begin() + scroll_top, make_blank_row());
        }
    }

    // ── Move cursor with wrap ─────────────────────────────────────────────
    void advance_cursor() {
        if (cursor_col + 1 < cols) {
            ++cursor_col;
            pending_wrap = false;
        } else {
            pending_wrap = true; // wrap on next printable char
        }
    }

    void newline(bool carriage_return) {
        if (carriage_return) cursor_col = 0;
        pending_wrap = false;
        if (cursor_row == scroll_bottom) {
            scroll_up(1);
        } else if (cursor_row < rows - 1) {
            ++cursor_row;
        }
    }

    // ── Write codepoint at cursor ─────────────────────────────────────────
    void put_char(uint32_t cp) {
        if (pending_wrap && autowrap) {
            cursor_col   = 0;
            pending_wrap = false;
            if (cursor_row == scroll_bottom) scroll_up(1);
            else if (cursor_row < rows - 1) ++cursor_row;
        }
        clamp_cursor();
        Cell& c    = at(cursor_row, cursor_col);
        c.cp       = cp;
        c.fg       = pen.fg;
        c.bg       = pen.bg;
        c.attrs    = pen.attrs;
        advance_cursor();
    }

    // ── Erase ─────────────────────────────────────────────────────────────

    void erase_cells(int r, int c_from, int c_to) {
        // c_to is inclusive
        c_from = std::clamp(c_from, 0, cols - 1);
        c_to   = std::clamp(c_to,   0, cols - 1);
        for (int c = c_from; c <= c_to; ++c) {
            at(r, c) = blank_cell();
        }
    }

    void erase_line(int r, int mode) {
        switch (mode) {
            case 0: erase_cells(r, cursor_col, cols - 1); break; // to end
            case 1: erase_cells(r, 0, cursor_col);        break; // to start
            case 2: erase_cells(r, 0, cols - 1);          break; // all
        }
    }

    void erase_display(int mode) {
        switch (mode) {
            case 0: // cursor to end
                erase_cells(cursor_row, cursor_col, cols - 1);
                for (int r = cursor_row + 1; r < rows; ++r)
                    erase_cells(r, 0, cols - 1);
                break;
            case 1: // start to cursor
                for (int r = 0; r < cursor_row; ++r)
                    erase_cells(r, 0, cols - 1);
                erase_cells(cursor_row, 0, cursor_col);
                break;
            case 2: // all
                for (int r = 0; r < rows; ++r)
                    erase_cells(r, 0, cols - 1);
                break;
            case 3: // all + clear scrollback
                for (int r = 0; r < rows; ++r)
                    erase_cells(r, 0, cols - 1);
                scrollback.clear();
                break;
        }
    }

    // ── SGR colour helper ─────────────────────────────────────────────────
    // Standard 16-color table (xterm defaults)
    static uint32_t ansi16_to_rgb(int index) {
        static const uint32_t table[16] = {
            0x000000, 0xAA0000, 0x00AA00, 0xAA5500, // 0-3: normal
            0x0000AA, 0xAA00AA, 0x00AAAA, 0xAAAAAA, // 4-7
            0x555555, 0xFF5555, 0x55FF55, 0xFFFF55, // 8-11: bright
            0x5555FF, 0xFF55FF, 0x55FFFF, 0xFFFFFF, // 12-15
        };
        return (index >= 0 && index < 16) ? table[index] : 0x000000;
    }

    // ── CSI dispatch ─────────────────────────────────────────────────────
    void dispatch_csi(char final_byte) {
        int p0 = (csi_param_count > 0) ? csi_params[0] : 0;
        int p1 = (csi_param_count > 1) ? csi_params[1] : 0;

        if (csi_priv) {
            // ── DEC Private (?h / ?l) ──────────────────────────────────
            bool set_mode = (final_byte == 'h');
            switch (p0) {
                case 1:  /* DECCKM — cursor keys, tracked but not sent to Kotlin */ break;
                case 7:  autowrap       = set_mode; break;
                case 25: cursor_visible = set_mode; break;
                case 47: case 1047: case 1049:
                    if (set_mode) enter_alt_screen();
                    else          exit_alt_screen();
                    break;
            }
            return;
        }

        switch (final_byte) {
            // ── Cursor movement ───────────────────────────────────────
            case 'A': cursor_row = std::max(scroll_top, cursor_row - std::max(p0,1)); pending_wrap = false; break;
            case 'B': cursor_row = std::min(scroll_bottom, cursor_row + std::max(p0,1)); pending_wrap = false; break;
            case 'C': cursor_col = std::min(cols-1, cursor_col + std::max(p0,1)); pending_wrap = false; break;
            case 'D': cursor_col = std::max(0, cursor_col - std::max(p0,1)); pending_wrap = false; break;
            case 'E': cursor_row = std::min(rows-1, cursor_row + std::max(p0,1)); cursor_col = 0; pending_wrap = false; break;
            case 'F': cursor_row = std::max(0, cursor_row - std::max(p0,1)); cursor_col = 0; pending_wrap = false; break;
            case 'G': cursor_col = std::clamp(std::max(p0,1) - 1, 0, cols-1); pending_wrap = false; break;
            case 'H': case 'f': // CUP / HVP
                cursor_row = std::clamp(std::max(p0,1) - 1, 0, rows-1);
                cursor_col = std::clamp(std::max(p1,1) - 1, 0, cols-1);
                pending_wrap = false;
                break;
            case 'd': cursor_row = std::clamp(std::max(p0,1) - 1, 0, rows-1); pending_wrap = false; break;

            // ── Erase ─────────────────────────────────────────────────
            case 'J': erase_display(p0); break;
            case 'K': erase_line(cursor_row, p0); break;
            case 'X': // ECH — erase n chars from cursor
                erase_cells(cursor_row, cursor_col,
                             std::min(cols-1, cursor_col + std::max(p0,1) - 1));
                break;

            // ── Line operations ───────────────────────────────────────
            case 'L': // IL — insert lines
            {
                int n = std::max(p0, 1);
                for (int i = 0; i < n; ++i) {
                    if (scroll_bottom < rows-1)
                        grid.erase(grid.begin() + scroll_bottom);
                    else
                        grid.erase(grid.begin() + scroll_bottom);
                    grid.insert(grid.begin() + cursor_row, make_blank_row());
                }
                break;
            }
            case 'M': // DL — delete lines
                for (int i = 0; i < std::max(p0,1); ++i) {
                    grid.erase(grid.begin() + cursor_row);
                    grid.insert(grid.begin() + scroll_bottom, make_blank_row());
                }
                break;

            // ── Char operations ───────────────────────────────────────
            case '@': // ICH — insert chars
            {
                int n = std::max(p0, 1);
                Row& row = grid[static_cast<size_t>(cursor_row)];
                row.insert(row.begin() + cursor_col, n, blank_cell());
                row.resize(static_cast<size_t>(cols), blank_cell());
                break;
            }
            case 'P': // DCH — delete chars
            {
                int n = std::max(p0, 1);
                Row& row = grid[static_cast<size_t>(cursor_row)];
                int from = std::min(cursor_col + n, cols);
                row.erase(row.begin() + cursor_col, row.begin() + from);
                row.resize(static_cast<size_t>(cols), blank_cell());
                break;
            }

            // ── Scroll ────────────────────────────────────────────────
            case 'S': scroll_up(std::max(p0, 1));   break;
            case 'T': scroll_down(std::max(p0, 1)); break;

            // ── SGR — Select Graphic Rendition ────────────────────────
            case 'm': dispatch_sgr(); break;

            // ── Scroll region ─────────────────────────────────────────
            case 'r': // DECSTBM
            {
                int top = std::max(p0, 1) - 1;
                int bot = (p1 == 0 ? rows : p1) - 1;
                if (top < bot && bot < rows) {
                    scroll_top    = top;
                    scroll_bottom = bot;
                }
                cursor_row = 0; cursor_col = 0; pending_wrap = false;
                break;
            }

            // ── Save / restore cursor ─────────────────────────────────
            case 's': saved = { cursor_row, cursor_col, pen }; break;
            case 'u': cursor_row = saved.row; cursor_col = saved.col; pen = saved.pen; break;

            // ── Device status report ──────────────────────────────────
            case 'n': // DSR — we don't write back to PTY from here; Kotlin handles it
                break;

            default: break;
        }
    }

    // ── SGR ───────────────────────────────────────────────────────────────
    void dispatch_sgr() {
        if (csi_param_count == 0) { reset_pen(); return; }

        for (int i = 0; i < csi_param_count; ) {
            int p = csi_params[i];
            switch (p) {
                case 0:  reset_pen(); ++i; break;
                case 1:  pen.attrs |=  ATTR_BOLD;          ++i; break;
                case 2:  pen.attrs |=  ATTR_DIM;           ++i; break;
                case 3:  pen.attrs |=  ATTR_ITALIC;        ++i; break;
                case 4:  pen.attrs |=  ATTR_UNDERLINE;     ++i; break;
                case 5:  pen.attrs |=  ATTR_BLINK;        ++i; break;
                case 7:  pen.attrs |=  ATTR_REVERSE;       ++i; break;
                case 8:  pen.attrs |=  ATTR_INVISIBLE;     ++i; break;
                case 9:  pen.attrs |=  ATTR_STRIKETHROUGH; ++i; break;
                case 22: pen.attrs &= ~(ATTR_BOLD|ATTR_DIM);    ++i; break;
                case 23: pen.attrs &= ~ATTR_ITALIC;        ++i; break;
                case 24: pen.attrs &= ~ATTR_UNDERLINE;     ++i; break;
                case 25: pen.attrs &= ~ATTR_BLINK;        ++i; break;
                case 27: pen.attrs &= ~ATTR_REVERSE;       ++i; break;
                case 28: pen.attrs &= ~ATTR_INVISIBLE;     ++i; break;
                case 29: pen.attrs &= ~ATTR_STRIKETHROUGH; ++i; break;
                case 39: pen.fg = COLOR_DEFAULT;           ++i; break;
                case 49: pen.bg = COLOR_DEFAULT;           ++i; break;

                default:
                    if (p >= 30 && p <= 37) { pen.fg = COLOR_INDEXED | static_cast<uint32_t>(p-30);   ++i; break; }
                    if (p >= 40 && p <= 47) { pen.bg = COLOR_INDEXED | static_cast<uint32_t>(p-40);   ++i; break; }
                    if (p >= 90 && p <= 97) { pen.fg = COLOR_INDEXED | static_cast<uint32_t>(p-90+8); ++i; break; }
                    if (p >= 100 && p <= 107) { pen.bg = COLOR_INDEXED | static_cast<uint32_t>(p-100+8); ++i; break; }

                    if ((p == 38 || p == 48) && i + 1 < csi_param_count) {
                        uint32_t& target = (p == 38) ? pen.fg : pen.bg;
                        int mode = csi_params[i + 1];
                        if (mode == 5 && i + 2 < csi_param_count) {
                            // 256-color indexed
                            target = COLOR_INDEXED | static_cast<uint32_t>(csi_params[i+2] & 0xFF);
                            i += 3;
                        } else if (mode == 2 && i + 4 < csi_param_count) {
                            // 24-bit truecolor
                            uint32_t r = static_cast<uint32_t>(csi_params[i+2] & 0xFF);
                            uint32_t g = static_cast<uint32_t>(csi_params[i+3] & 0xFF);
                            uint32_t b = static_cast<uint32_t>(csi_params[i+4] & 0xFF);
                            target = (r << 16) | (g << 8) | b;
                            i += 5;
                        } else { ++i; }
                    } else { ++i; }
                    break;
            }
        }
    }

    void reset_pen() {
        pen = Pen{};
    }

    // ── OSC dispatch ─────────────────────────────────────────────────────
    void dispatch_osc() {
        // Format: "N;text" where N is the command number
        const char* semi = static_cast<const char*>(memchr(osc_buf, ';', static_cast<size_t>(osc_len)));
        if (!semi) return;
        int cmd = atoi(osc_buf);
        if (cmd == 0 || cmd == 2) {
            // Window title
            title.assign(semi + 1, static_cast<size_t>(osc_len - (semi - osc_buf + 1)));
        }
    }

    // ── Simple escape dispatch ────────────────────────────────────────────
    void dispatch_esc(char ch) {
        switch (ch) {
            case '7': saved = { cursor_row, cursor_col, pen }; break;    // DECSC
            case '8':                                                       // DECRC
                cursor_row = saved.row;
                cursor_col = saved.col;
                pen        = saved.pen;
                pending_wrap = false;
                break;
            case 'c': full_reset(); break;                                  // RIS
            case 'D': newline(false); break;                                // IND
            case 'E': newline(true); break;                                 // NEL
            case 'M':                                                        // RI (reverse index)
                if (cursor_row == scroll_top) scroll_down(1);
                else if (cursor_row > 0) --cursor_row;
                break;
            default: break;
        }
    }

    // ── Alternate screen ─────────────────────────────────────────────────
    void enter_alt_screen() {
        if (alt_screen) return;
        alt_screen = true;
        alt_grid   = grid;                    // save current screen
        // clear the alt grid
        grid.assign(static_cast<size_t>(rows), make_blank_row());
        scroll_top    = 0;
        scroll_bottom = rows - 1;
    }

    void exit_alt_screen() {
        if (!alt_screen) return;
        alt_screen = false;
        grid = alt_grid;
        alt_grid.clear();
    }

    // ── Full reset (RIS) ──────────────────────────────────────────────────
    void full_reset() {
        grid.assign(static_cast<size_t>(rows), make_blank_row());
        scrollback.clear();
        cursor_row    = 0;
        cursor_col    = 0;
        pending_wrap  = false;
        cursor_visible = true;
        autowrap      = true;
        scroll_top    = 0;
        scroll_bottom = rows - 1;
        alt_screen    = false;
        reset_pen();
        state         = State::Ground;
        title.clear();
    }

    // ── Resize ────────────────────────────────────────────────────────────
    void resize(int new_rows, int new_cols) {
        if (new_rows == rows && new_cols == cols) return;

        // Expand / shrink columns in every row
        for (auto& row : grid) {
            row.resize(static_cast<size_t>(new_cols), blank_cell());
        }
        // Expand / shrink row count
        while (static_cast<int>(grid.size()) < new_rows)
            grid.push_back(make_blank_row());
        if (static_cast<int>(grid.size()) > new_rows)
            grid.resize(static_cast<size_t>(new_rows));

        rows          = new_rows;
        cols          = new_cols;
        scroll_bottom = rows - 1;
        scroll_top    = 0;
        cursor_row    = std::clamp(cursor_row, 0, rows - 1);
        cursor_col    = std::clamp(cursor_col, 0, cols - 1);
        pending_wrap  = false;
    }

    // ── UTF-8 decoder ─────────────────────────────────────────────────────
    // Returns codepoint when sequence complete, 0 to keep buffering, -1 on error.
    int utf8_feed(uint8_t byte) {
        if (byte < 0x80) {
            utf8_remaining = 0;
            return static_cast<int>(byte);
        }
        if ((byte & 0xC0) == 0x80) { // continuation byte
            if (utf8_remaining <= 0) return 0xFFFD; // replacement char
            utf8_codepoint = (utf8_codepoint << 6) | (byte & 0x3F);
            --utf8_remaining;
            if (utf8_remaining == 0) {
                uint32_t cp = utf8_codepoint;
                utf8_codepoint = 0;
                return static_cast<int>(cp);
            }
            return 0; // not done yet
        }
        // Start byte
        if ((byte & 0xE0) == 0xC0) { utf8_codepoint = byte & 0x1F; utf8_remaining = 1; }
        else if ((byte & 0xF0) == 0xE0) { utf8_codepoint = byte & 0x0F; utf8_remaining = 2; }
        else if ((byte & 0xF8) == 0xF0) { utf8_codepoint = byte & 0x07; utf8_remaining = 3; }
        else { utf8_remaining = 0; return 0xFFFD; }
        return 0; // waiting for continuations
    }

    // ── Feed bytes ────────────────────────────────────────────────────────
    void feed(const uint8_t* data, size_t len) {
        for (size_t i = 0; i < len; ++i) {
            process_byte(data[i]);
        }
    }

    void process_byte(uint8_t byte) {
        // C0 controls handled in most states
        if (byte == 0x1B) { // ESC — always transitions
            state = State::Escape;
            csi_param_count = 0;
            csi_inter_len   = 0;
            csi_priv        = false;
            utf8_remaining  = 0;
            return;
        }
        if (byte == 0x18 || byte == 0x1A) { // CAN / SUB — cancel sequence
            state = State::Ground;
            return;
        }

        switch (state) {
            case State::Ground:
                process_ground(byte);
                break;

            case State::Escape:
                process_escape(byte);
                break;

            case State::EscapeIntermediate:
                if (byte >= 0x20 && byte <= 0x2F) {
                    // collect intermediate
                } else if (byte >= 0x30 && byte <= 0x7E) {
                    dispatch_esc(static_cast<char>(byte));
                    state = State::Ground;
                } else {
                    state = State::Ground;
                }
                break;

            case State::CsiEntry:
                csi_param_count = 0;
                csi_inter_len   = 0;
                csi_priv        = false;
                state = State::CsiParam;
                process_csi_param(byte);
                break;

            case State::CsiParam:
                process_csi_param(byte);
                break;

            case State::CsiIntermediate:
                if (byte >= 0x40 && byte <= 0x7E) {
                    dispatch_csi(static_cast<char>(byte));
                    state = State::Ground;
                }
                break;

            case State::CsiIgnore:
                if (byte >= 0x40 && byte <= 0x7E) state = State::Ground;
                break;

            case State::OscString:
                process_osc(byte);
                break;

            case State::DcsIgnore:
                if (byte == 0x9C || (byte == 0x5C && state == State::Escape))
                    state = State::Ground;
                break;

            case State::SosPmApcString:
                if (byte == 0x9C) state = State::Ground;
                break;

            case State::Utf8Continuation:
                // handled inside process_ground
                break;
        }
    }

    void process_ground(uint8_t byte) {
        if (utf8_remaining > 0) {
            int cp = utf8_feed(byte);
            if (cp > 0) put_char(static_cast<uint32_t>(cp));
            return;
        }

        if (byte < 0x20) {
            // C0 control
            switch (byte) {
                case 0x07: break;            // BEL — ignore
                case 0x08:                   // BS
                    if (cursor_col > 0) { --cursor_col; pending_wrap = false; }
                    break;
                case 0x09:                   // HT (tab)
                    cursor_col = std::min(cols - 1, (cursor_col / 8 + 1) * 8);
                    pending_wrap = false;
                    break;
                case 0x0A: case 0x0B: case 0x0C: // LF, VT, FF
                    newline(false);
                    break;
                case 0x0D:                   // CR
                    cursor_col   = 0;
                    pending_wrap = false;
                    break;
                case 0x0E: case 0x0F: break; // SO/SI — charset shift, ignore
            }
            return;
        }

        if (byte == 0x7F) return; // DEL — ignore

        // Printable ASCII
        if (byte < 0x80) {
            put_char(static_cast<uint32_t>(byte));
            return;
        }

        // Start of multi-byte UTF-8
        int cp = utf8_feed(byte);
        if (cp > 0) put_char(static_cast<uint32_t>(cp));
        // else waiting for continuation bytes
    }

    void process_escape(uint8_t byte) {
        if (byte == '[') { state = State::CsiEntry; return; }
        if (byte == ']') { state = State::OscString; osc_len = 0; return; }
        if (byte == 'P') { state = State::DcsIgnore; return; }
        if (byte == 'X' || byte == '^' || byte == '_') { state = State::SosPmApcString; return; }
        if (byte >= 0x20 && byte <= 0x2F) { state = State::EscapeIntermediate; return; }
        if (byte >= 0x30 && byte <= 0x7E) {
            dispatch_esc(static_cast<char>(byte));
        }
        state = State::Ground;
    }

    void process_csi_param(uint8_t byte) {
        if (byte == '?') { csi_priv = true; return; }
        if (byte >= '0' && byte <= '9') {
            int& last = (csi_param_count == 0)
                ? (csi_param_count = 1, csi_params[0] = 0, csi_params[0])
                : csi_params[csi_param_count - 1];
            last = last * 10 + (byte - '0');
            return;
        }
        if (byte == ';') {
            if (csi_param_count < MAX_CSI_PARAMS) {
                if (csi_param_count == 0) csi_param_count = 1; // ensure p0 exists
                ++csi_param_count;
                if (csi_param_count < MAX_CSI_PARAMS)
                    csi_params[csi_param_count - 1] = 0;
            }
            return;
        }
        if (byte == ':') return; // sub-param separator — treat as ';' equivalent
        if (byte >= 0x20 && byte <= 0x2F) { state = State::CsiIntermediate; return; }
        if (byte >= 0x40 && byte <= 0x7E) {
            if (csi_param_count == 0) csi_param_count = 1, csi_params[0] = 0;
            dispatch_csi(static_cast<char>(byte));
            state = State::Ground;
            return;
        }
        if (byte >= 0x70 && byte <= 0x7E) { state = State::CsiIgnore; return; } // private final
        state = State::CsiIgnore;
    }

    void process_osc(uint8_t byte) {
        if (byte == 0x07) { // BEL terminates OSC
            dispatch_osc();
            state = State::Ground;
            return;
        }
        if (byte == 0x9C) { // ST (String Terminator)
            dispatch_osc();
            state = State::Ground;
            return;
        }
        if (byte == 0x1B) { // ESC (start of ST = ESC \)
            // handled on next byte (0x5C = '\')
            return;
        }
        if (byte == 0x5C && osc_len > 0) { // '\' completing ESC\
            dispatch_osc();
            state = State::Ground;
            return;
        }
        if (osc_len < MAX_OSC_LEN - 1) {
            osc_buf[osc_len++] = static_cast<char>(byte);
        }
    }
};

// ── JNI exports ───────────────────────────────────────────────────────────────
extern "C" {

/**
 * long nativeCreate(rows: Int, cols: Int): Long
 * Returns opaque heap pointer cast to jlong. Kotlin keeps this as a handle.
 */
JNIEXPORT jlong JNICALL
Java_com_jarvis_android_system_terminal_VtParser_nativeCreate(
        JNIEnv* /* env */, jobject /* thiz */, jint rows, jint cols) {
    auto* t = new Terminal(std::max(rows, 1), std::max(cols, 1));
    return reinterpret_cast<jlong>(t);
}

/**
 * void nativeFeed(handle: Long, data: ByteArray, length: Int)
 * Feeds raw PTY output bytes into the parser.
 */
JNIEXPORT void JNICALL
Java_com_jarvis_android_system_terminal_VtParser_nativeFeed(
        JNIEnv* env, jobject /* thiz */, jlong handle, jbyteArray data, jint length) {
    if (handle == 0 || data == nullptr || length <= 0) return;
    auto* t = reinterpret_cast<Terminal*>(handle);
    jbyte* buf = env->GetByteArrayElements(data, nullptr);
    if (!buf) return;
    t->feed(reinterpret_cast<const uint8_t*>(buf), static_cast<size_t>(length));
    env->ReleaseByteArrayElements(data, buf, JNI_ABORT);
}

/**
 * ByteArray nativeGetGrid(handle: Long): ByteArray
 *
 * Returns the visible grid as a flat byte array.
 * Each cell = 13 bytes (see file header for encoding).
 * Total size = rows * cols * 13.
 */
JNIEXPORT jbyteArray JNICALL
Java_com_jarvis_android_system_terminal_VtParser_nativeGetGrid(
        JNIEnv* env, jobject /* thiz */, jlong handle) {
    if (handle == 0) return nullptr;
    auto* t    = reinterpret_cast<Terminal*>(handle);
    int   size = t->rows * t->cols * BYTES_PER_CELL;
    jbyteArray result = env->NewByteArray(size);
    if (!result) return nullptr;

    jbyte* buf = env->GetByteArrayElements(result, nullptr);
    if (!buf) return result;

    jbyte* ptr = buf;
    for (int r = 0; r < t->rows; ++r) {
        const Row& row = t->grid[static_cast<size_t>(r)];
        for (int c = 0; c < t->cols; ++c) {
            const Cell& cell = row[static_cast<size_t>(c)];
            // codepoint — 4 bytes LE
            ptr[0] = static_cast<jbyte>(cell.cp & 0xFF);
            ptr[1] = static_cast<jbyte>((cell.cp >> 8) & 0xFF);
            ptr[2] = static_cast<jbyte>((cell.cp >> 16) & 0xFF);
            ptr[3] = static_cast<jbyte>((cell.cp >> 24) & 0xFF);
            // fg — 4 bytes LE
            ptr[4] = static_cast<jbyte>(cell.fg & 0xFF);
            ptr[5] = static_cast<jbyte>((cell.fg >> 8) & 0xFF);
            ptr[6] = static_cast<jbyte>((cell.fg >> 16) & 0xFF);
            ptr[7] = static_cast<jbyte>((cell.fg >> 24) & 0xFF);
            // bg — 4 bytes LE
            ptr[8]  = static_cast<jbyte>(cell.bg & 0xFF);
            ptr[9]  = static_cast<jbyte>((cell.bg >> 8) & 0xFF);
            ptr[10] = static_cast<jbyte>((cell.bg >> 16) & 0xFF);
            ptr[11] = static_cast<jbyte>((cell.bg >> 24) & 0xFF);
            // attrs — 1 byte
            ptr[12] = static_cast<jbyte>(cell.attrs);
            ptr += BYTES_PER_CELL;
        }
    }

    env->ReleaseByteArrayElements(result, buf, 0);
    return result;
}

/** int nativeGetCursorRow(handle: Long): Int */
JNIEXPORT jint JNICALL
Java_com_jarvis_android_system_terminal_VtParser_nativeGetCursorRow(
        JNIEnv* /* env */, jobject /* thiz */, jlong handle) {
    if (handle == 0) return 0;
    return reinterpret_cast<Terminal*>(handle)->cursor_row;
}

/** int nativeGetCursorCol(handle: Long): Int */
JNIEXPORT jint JNICALL
Java_com_jarvis_android_system_terminal_VtParser_nativeGetCursorCol(
        JNIEnv* /* env */, jobject /* thiz */, jlong handle) {
    if (handle == 0) return 0;
    return reinterpret_cast<Terminal*>(handle)->cursor_col;
}

/** boolean nativeIsCursorVisible(handle: Long): Boolean */
JNIEXPORT jboolean JNICALL
Java_com_jarvis_android_system_terminal_VtParser_nativeIsCursorVisible(
        JNIEnv* /* env */, jobject /* thiz */, jlong handle) {
    if (handle == 0) return JNI_TRUE;
    return reinterpret_cast<Terminal*>(handle)->cursor_visible
        ? JNI_TRUE : JNI_FALSE;
}

/** String nativeGetTitle(handle: Long): String */
JNIEXPORT jstring JNICALL
Java_com_jarvis_android_system_terminal_VtParser_nativeGetTitle(
        JNIEnv* env, jobject /* thiz */, jlong handle) {
    if (handle == 0) return env->NewStringUTF("");
    const std::string& title = reinterpret_cast<Terminal*>(handle)->title;
    return env->NewStringUTF(title.c_str());
}

/**
 * int nativeGetScrollbackSize(handle: Long): Int
 * Number of lines in the scrollback buffer above the visible screen.
 */
JNIEXPORT jint JNICALL
Java_com_jarvis_android_system_terminal_VtParser_nativeGetScrollbackSize(
        JNIEnv* /* env */, jobject /* thiz */, jlong handle) {
    if (handle == 0) return 0;
    return static_cast<jint>(
        reinterpret_cast<Terminal*>(handle)->scrollback.size());
}

/**
 * ByteArray nativeGetScrollbackRow(handle: Long, index: Int): ByteArray
 * Returns a single scrollback row at the given index (0 = oldest).
 * Same encoding as nativeGetGrid rows.
 */
JNIEXPORT jbyteArray JNICALL
Java_com_jarvis_android_system_terminal_VtParser_nativeGetScrollbackRow(
        JNIEnv* env, jobject /* thiz */, jlong handle, jint index) {
    if (handle == 0) return nullptr;
    auto* t = reinterpret_cast<Terminal*>(handle);
    if (index < 0 || index >= static_cast<int>(t->scrollback.size())) return nullptr;

    const Row& row = t->scrollback[static_cast<size_t>(index)];
    int col_count  = std::min(static_cast<int>(row.size()), t->cols);
    jbyteArray result = env->NewByteArray(col_count * BYTES_PER_CELL);
    if (!result) return nullptr;

    jbyte* buf = env->GetByteArrayElements(result, nullptr);
    if (!buf) return result;
    jbyte* ptr = buf;

    for (int c = 0; c < col_count; ++c) {
        const Cell& cell = row[static_cast<size_t>(c)];
        ptr[0] = static_cast<jbyte>(cell.cp & 0xFF);
        ptr[1] = static_cast<jbyte>((cell.cp >> 8) & 0xFF);
        ptr[2] = static_cast<jbyte>((cell.cp >> 16) & 0xFF);
        ptr[3] = static_cast<jbyte>((cell.cp >> 24) & 0xFF);
        ptr[4] = static_cast<jbyte>(cell.fg & 0xFF);
        ptr[5] = static_cast<jbyte>((cell.fg >> 8) & 0xFF);
        ptr[6] = static_cast<jbyte>((cell.fg >> 16) & 0xFF);
        ptr[7] = static_cast<jbyte>((cell.fg >> 24) & 0xFF);
        ptr[8]  = static_cast<jbyte>(cell.bg & 0xFF);
        ptr[9]  = static_cast<jbyte>((cell.bg >> 8) & 0xFF);
        ptr[10] = static_cast<jbyte>((cell.bg >> 16) & 0xFF);
        ptr[11] = static_cast<jbyte>((cell.bg >> 24) & 0xFF);
        ptr[12] = static_cast<jbyte>(cell.attrs);
        ptr += BYTES_PER_CELL;
    }

    env->ReleaseByteArrayElements(result, buf, 0);
    return result;
}

/**
 * void nativeResize(handle: Long, rows: Int, cols: Int)
 * Called when the terminal Composable size changes.
 */
JNIEXPORT void JNICALL
Java_com_jarvis_android_system_terminal_VtParser_nativeResize(
        JNIEnv* /* env */, jobject /* thiz */, jlong handle, jint rows, jint cols) {
    if (handle == 0 || rows <= 0 || cols <= 0) return;
    reinterpret_cast<Terminal*>(handle)->resize(rows, cols);
}

/**
 * void nativeDestroy(handle: Long)
 * Deletes the Terminal object. Call from VtParser.close() or finalize().
 */
JNIEXPORT void JNICALL
Java_com_jarvis_android_system_terminal_VtParser_nativeDestroy(
        JNIEnv* /* env */, jobject /* thiz */, jlong handle) {
    if (handle == 0) return;
    delete reinterpret_cast<Terminal*>(handle);
}

} // extern "C"
