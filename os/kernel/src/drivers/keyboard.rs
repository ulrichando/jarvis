//! PS/2 Keyboard Driver
//!
//! Handles keyboard interrupts and translates scancodes to characters.
//! Feeds input to the JARVIS shell / IPC system.

use crate::{fbprint, fbprintln};
use pc_keyboard::{layouts, DecodedKey, HandleControl, Keyboard, ScancodeSet1};
use spin::Mutex;
use x86_64::instructions::port::Port;

static KEYBOARD: Mutex<Option<Keyboard<layouts::Us104Key, ScancodeSet1>>> = Mutex::new(None);

/// Input buffer — stores keystrokes for the shell
const INPUT_BUF_SIZE: usize = 256;
static INPUT_BUFFER: Mutex<InputBuffer> = Mutex::new(InputBuffer::new());

struct InputBuffer {
    buf: [u8; INPUT_BUF_SIZE],
    head: usize,
    tail: usize,
}

impl InputBuffer {
    const fn new() -> Self {
        InputBuffer {
            buf: [0; INPUT_BUF_SIZE],
            head: 0,
            tail: 0,
        }
    }

    fn push(&mut self, byte: u8) {
        let next = (self.head + 1) % INPUT_BUF_SIZE;
        if next != self.tail {
            self.buf[self.head] = byte;
            self.head = next;
        }
    }

    fn pop(&mut self) -> Option<u8> {
        if self.head == self.tail {
            None
        } else {
            let byte = self.buf[self.tail];
            self.tail = (self.tail + 1) % INPUT_BUF_SIZE;
            Some(byte)
        }
    }
}

pub fn init() {
    *KEYBOARD.lock() = Some(Keyboard::new(
        ScancodeSet1::new(),
        layouts::Us104Key,
        HandleControl::Ignore,
    ));
}

/// Called from the keyboard interrupt handler
pub fn handle_scancode(scancode: u8) {
    let mut kb = KEYBOARD.lock();
    if let Some(ref mut keyboard) = *kb {
        if let Ok(Some(key_event)) = keyboard.add_byte(scancode) {
            if let Some(key) = keyboard.process_keyevent(key_event) {
                match key {
                    DecodedKey::Unicode(character) => {
                        INPUT_BUFFER.lock().push(character as u8);
                        // Echo to screen
                        if character == '\n' {
                            fbprintln!();
                        } else {
                            fbprint!("{}", character);
                        }
                    }
                    DecodedKey::RawKey(_key) => {
                        // Handle special keys (arrows, function keys, etc.)
                    }
                }
            }
        }
    }
}

/// Read a byte from the keyboard input buffer (non-blocking)
pub fn read_byte() -> Option<u8> {
    x86_64::instructions::interrupts::without_interrupts(|| INPUT_BUFFER.lock().pop())
}

/// Read a scancode from the PS/2 port
pub fn read_scancode() -> u8 {
    let mut port = Port::new(0x60);
    unsafe { port.read() }
}
