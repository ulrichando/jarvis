//! VGA Text Mode Driver — Screen output for JARVIS
//!
//! 80x25 text mode with JARVIS color theming.
//! Uses raw volatile writes for VGA buffer access.

use core::fmt;
use spin::Mutex;

const BUFFER_HEIGHT: usize = 25;
const BUFFER_WIDTH: usize = 80;
const VGA_BUFFER_ADDR: usize = 0xb8000;

/// VGA color codes
#[allow(dead_code)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum Color {
    Black = 0,
    Blue = 1,
    Green = 2,
    Cyan = 3,
    Red = 4,
    Magenta = 5,
    Brown = 6,
    LightGray = 7,
    DarkGray = 8,
    LightBlue = 9,
    LightGreen = 10,
    LightCyan = 11,
    LightRed = 12,
    Pink = 13,
    Yellow = 14,
    White = 15,
}

/// Color theme presets
pub enum Theme {
    Jarvis, // Cyan on dark — the JARVIS look
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(transparent)]
struct ColorCode(u8);

impl ColorCode {
    const fn new(foreground: Color, background: Color) -> ColorCode {
        ColorCode((background as u8) << 4 | (foreground as u8))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(C)]
struct ScreenChar {
    ascii_character: u8,
    color_code: ColorCode,
}

pub struct Writer {
    column_position: usize,
    row_position: usize,
    color_code: ColorCode,
}

impl Writer {
    /// Write a ScreenChar to VGA buffer using volatile write
    #[inline]
    fn write_vga(&mut self, row: usize, col: usize, ch: ScreenChar) {
        let offset = (row * BUFFER_WIDTH + col) * 2;
        let ptr = (VGA_BUFFER_ADDR + offset) as *mut u16;
        let val = (ch.color_code.0 as u16) << 8 | (ch.ascii_character as u16);
        unsafe {
            core::ptr::write_volatile(ptr, val);
        }
    }

    /// Read a ScreenChar from VGA buffer
    #[inline]
    fn read_vga(&self, row: usize, col: usize) -> ScreenChar {
        let offset = (row * BUFFER_WIDTH + col) * 2;
        let ptr = (VGA_BUFFER_ADDR + offset) as *const u16;
        let val = unsafe { core::ptr::read_volatile(ptr) };
        ScreenChar {
            ascii_character: (val & 0xFF) as u8,
            color_code: ColorCode((val >> 8) as u8),
        }
    }

    fn write_byte(&mut self, byte: u8) {
        match byte {
            b'\n' => self.new_line(),
            byte => {
                if self.column_position >= BUFFER_WIDTH {
                    self.new_line();
                }
                let row = self.row_position;
                let col = self.column_position;
                self.write_vga(row, col, ScreenChar {
                    ascii_character: byte,
                    color_code: self.color_code,
                });
                self.column_position += 1;
            }
        }
    }

    fn write_string(&mut self, s: &str) {
        for byte in s.bytes() {
            match byte {
                0x20..=0x7e | b'\n' => self.write_byte(byte),
                _ => self.write_byte(0xfe), // ■ for unprintable
            }
        }
    }

    fn new_line(&mut self) {
        if self.row_position < BUFFER_HEIGHT - 1 {
            self.row_position += 1;
        } else {
            // Scroll up
            for row in 1..BUFFER_HEIGHT {
                for col in 0..BUFFER_WIDTH {
                    let ch = self.read_vga(row, col);
                    self.write_vga(row - 1, col, ch);
                }
            }
            self.clear_row(BUFFER_HEIGHT - 1);
        }
        self.column_position = 0;
    }

    fn clear_row(&mut self, row: usize) {
        let blank = ScreenChar {
            ascii_character: b' ',
            color_code: self.color_code,
        };
        for col in 0..BUFFER_WIDTH {
            self.write_vga(row, col, blank);
        }
    }

    pub fn clear_screen(&mut self) {
        for row in 0..BUFFER_HEIGHT {
            self.clear_row(row);
        }
        self.column_position = 0;
        self.row_position = 0;
    }
}

impl fmt::Write for Writer {
    fn write_str(&mut self, s: &str) -> fmt::Result {
        self.write_string(s);
        Ok(())
    }
}

static WRITER: Mutex<Option<Writer>> = Mutex::new(None);

pub fn init() {
    let mut writer = Writer {
        column_position: 0,
        row_position: 0,
        color_code: ColorCode::new(Color::LightCyan, Color::Black),
    };
    writer.clear_screen();
    *WRITER.lock() = Some(writer);
}

pub fn set_theme(theme: Theme) {
    let color_code = match theme {
        Theme::Jarvis => ColorCode::new(Color::LightCyan, Color::Black),
    };
    if let Some(ref mut w) = *WRITER.lock() {
        w.color_code = color_code;
    }
}

pub fn set_color(fg: Color, bg: Color) {
    if let Some(ref mut w) = *WRITER.lock() {
        w.color_code = ColorCode::new(fg, bg);
    }
}

pub fn _print(args: fmt::Arguments) {
    use core::fmt::Write;
    // Disable interrupts while writing to VGA to prevent deadlocks
    x86_64::instructions::interrupts::without_interrupts(|| {
        if let Some(ref mut w) = *WRITER.lock() {
            let _ = w.write_fmt(args);
        }
    });
}

/// Print to VGA screen
#[macro_export]
macro_rules! vprint {
    ($($arg:tt)*) => ($crate::drivers::vga::_print(format_args!($($arg)*)));
}

/// Print line to VGA screen
#[macro_export]
macro_rules! vprintln {
    () => ($crate::vprint!("\n"));
    ($($arg:tt)*) => ($crate::vprint!("{}\n", format_args!($($arg)*)));
}
