// VGA text-mode driver — writes to the legacy 0xB8000 MMIO framebuffer.
// Every character is 2 bytes: the ASCII code and a color attribute.
//
// This exists because before we have a real framebuffer + font rasterizer, VGA
// text mode is the simplest way to print anything at all in 80x25 characters.

use core::fmt;
use lazy_static::lazy_static;
use spin::Mutex;
use volatile::Volatile;

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

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(transparent)]
pub struct ColorCode(u8);

impl ColorCode {
    pub fn new(foreground: Color, background: Color) -> ColorCode {
        ColorCode((background as u8) << 4 | (foreground as u8))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(C)]
struct ScreenChar {
    ascii: u8,
    color: ColorCode,
}

const BUFFER_HEIGHT: usize = 25;
const BUFFER_WIDTH: usize = 80;

#[repr(transparent)]
struct Buffer {
    chars: [[Volatile<ScreenChar>; BUFFER_WIDTH]; BUFFER_HEIGHT],
}

pub struct Writer {
    column: usize,
    color: ColorCode,
    buffer: &'static mut Buffer,
}

impl Writer {
    pub fn set_color(&mut self, color: ColorCode) {
        self.color = color;
    }

    pub fn write_byte(&mut self, byte: u8) {
        match byte {
            b'\n' => self.new_line(),
            byte => {
                if self.column >= BUFFER_WIDTH {
                    self.new_line();
                }
                let row = BUFFER_HEIGHT - 1;
                let col = self.column;
                self.buffer.chars[row][col].write(ScreenChar {
                    ascii: byte,
                    color: self.color,
                });
                self.column += 1;
            }
        }
    }

    fn write_string(&mut self, s: &str) {
        for b in s.bytes() {
            match b {
                0x20..=0x7e | b'\n' => self.write_byte(b),
                _ => self.write_byte(0xfe), // printable-fallback glyph
            }
        }
    }

    fn new_line(&mut self) {
        for row in 1..BUFFER_HEIGHT {
            for col in 0..BUFFER_WIDTH {
                let c = self.buffer.chars[row][col].read();
                self.buffer.chars[row - 1][col].write(c);
            }
        }
        self.clear_row(BUFFER_HEIGHT - 1);
        self.column = 0;
    }

    fn clear_row(&mut self, row: usize) {
        let blank = ScreenChar { ascii: b' ', color: self.color };
        for col in 0..BUFFER_WIDTH {
            self.buffer.chars[row][col].write(blank);
        }
    }
}

impl fmt::Write for Writer {
    fn write_str(&mut self, s: &str) -> fmt::Result {
        self.write_string(s);
        Ok(())
    }
}

lazy_static! {
    pub static ref WRITER: Mutex<Writer> = Mutex::new(Writer {
        column: 0,
        color: ColorCode::new(Color::LightGray, Color::Black),
        // SAFETY: the VGA text buffer is always at 0xB8000 on x86 PCs. No other
        // code maps or uses this region, so the &'static mut is sound for the
        // lifetime of the kernel (forever).
        buffer: unsafe { &mut *(0xB8000 as *mut Buffer) },
    });
}

pub fn clear() {
    let mut w = WRITER.lock();
    for row in 0..BUFFER_HEIGHT {
        w.clear_row(row);
    }
    w.column = 0;
}

#[macro_export]
macro_rules! print {
    ($($arg:tt)*) => ($crate::vga::_print(format_args!($($arg)*)));
}

#[macro_export]
macro_rules! println {
    () => ($crate::print!("\n"));
    ($($arg:tt)*) => ($crate::print!("{}\n", format_args!($($arg)*)));
}

#[doc(hidden)]
pub fn _print(args: fmt::Arguments) {
    use core::fmt::Write;
    // Disable interrupts while holding the lock to avoid deadlock if an ISR
    // also tries to print. (We don't have interrupts yet, but good habit.)
    x86_64::instructions::interrupts::without_interrupts(|| {
        WRITER.lock().write_fmt(args).unwrap();
    });
}
