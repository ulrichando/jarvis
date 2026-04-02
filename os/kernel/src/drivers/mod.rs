//! JARVIS Kernel Drivers
//!
//! Hardware abstraction for the JARVIS microkernel.
//! Each driver is minimal — just enough to interface with hardware.

pub mod framebuffer;
pub mod keyboard;
pub mod serial;
pub mod vga;
