//! Serial Port Driver — COM1 debug output
//!
//! Provides kprint!/kprintln! macros for kernel debug logging
//! over the serial port (visible in QEMU/VirtualBox debug console).

use spin::Mutex;
use uart_16550::SerialPort;

static SERIAL1: Mutex<Option<SerialPort>> = Mutex::new(None);

pub fn init() {
    let mut serial = unsafe { SerialPort::new(0x3F8) };
    serial.init();
    *SERIAL1.lock() = Some(serial);
}

pub fn _print(args: core::fmt::Arguments) {
    use core::fmt::Write;
    if let Some(ref mut serial) = *SERIAL1.lock() {
        let _ = serial.write_fmt(args);
    }
}

/// Print to serial console (kernel debug)
#[macro_export]
macro_rules! kprint {
    ($($arg:tt)*) => ($crate::drivers::serial::_print(format_args!($($arg)*)));
}

/// Print line to serial console (kernel debug)
#[macro_export]
macro_rules! kprintln {
    () => ($crate::kprint!("\n"));
    ($($arg:tt)*) => ($crate::kprint!("{}\n", format_args!($($arg)*)));
}
