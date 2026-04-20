// JARVIS — from-scratch x86_64 kernel.
//
// This is the ENTIRE kernel. No glibc, no libc, no std. We're `#![no_std]`
// because `std` assumes an OS underneath, and we ARE the OS. `#![no_main]`
// because the normal Rust main() signature assumes a runtime calls it — here
// the bootloader jumps directly to `_start` via the entry_point! macro.

#![no_std]
#![no_main]

use bootloader::{entry_point, BootInfo};
use core::panic::PanicInfo;

mod gdt;
mod interrupts;
mod serial;
mod vga;

entry_point!(kernel_main);

fn kernel_main(_boot_info: &'static BootInfo) -> ! {
    vga::clear();
    banner();
    serial_println!("[jarvis] kernel booted — VGA + serial online");

    gdt::init();
    interrupts::init();

    println!("  [ ok ] GDT + TSS installed");
    println!("  [ ok ] IDT armed, PICs remapped, interrupts enabled");
    println!("  [ ok ] keyboard listening on IRQ1");
    println!();
    println!("  > Type anything. JARVIS is listening.");
    println!();

    // hlt until next interrupt — saves the poor host CPU from spinning.
    loop {
        x86_64::instructions::hlt();
    }
}

fn banner() {
    use crate::vga::{Color, ColorCode, WRITER};
    use core::fmt::Write;
    let mut w = WRITER.lock();
    w.set_color(ColorCode::new(Color::White, Color::Black));
    let _ = writeln!(w);
    w.set_color(ColorCode::new(Color::LightCyan, Color::Black));
    let _ = writeln!(w, "       JJJJJ   AAAAA   RRRRR   V   V  III  SSSSS");
    let _ = writeln!(w, "         J    A     A  R    R  V   V   I   S    ");
    let _ = writeln!(w, "         J    AAAAAAA  RRRRR   V   V   I   SSSSS");
    let _ = writeln!(w, "       J J    A     A  R  R    V   V   I       S");
    let _ = writeln!(w, "        J     A     A  R   R    V V    I       S");
    let _ = writeln!(w, "                                 V    III  SSSSS");
    let _ = writeln!(w);
    w.set_color(ColorCode::new(Color::LightGray, Color::Black));
    let _ = writeln!(w, "       Just A Rather Very Intelligent System");
    let _ = writeln!(w, "       kernel 0.1.0 — x86_64 bare metal (Rust)");
    let _ = writeln!(w);
    w.set_color(ColorCode::new(Color::Green, Color::Black));
    let _ = writeln!(w, "  [ ok ] VGA text-mode driver online");
    let _ = writeln!(w, "  [ ok ] serial port COM1 online");
    let _ = writeln!(w, "  [ .. ] awaiting interrupt + keyboard drivers");
    let _ = writeln!(w);
    w.set_color(ColorCode::new(Color::LightCyan, Color::Black));
    let _ = writeln!(w, "  > Good morning. All systems are online.");
    w.set_color(ColorCode::new(Color::White, Color::Black));
}

#[panic_handler]
fn panic(info: &PanicInfo) -> ! {
    serial_println!("[jarvis] PANIC: {}", info);
    // Also show on VGA so the user sees it directly.
    println!("KERNEL PANIC: {}", info);
    loop {
        x86_64::instructions::hlt();
    }
}
