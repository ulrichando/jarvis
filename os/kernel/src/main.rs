//! JARVIS Microkernel — Entry Point
//!
//! A custom operating system kernel built from scratch for the
//! JARVIS Autonomous Intelligence system. No Linux, no POSIX.
//! Pure JARVIS.

#![no_std]
#![no_main]
#![feature(abi_x86_interrupt)]

extern crate alloc;

mod drivers;
mod interrupts;
mod ipc;
mod jarvis;
mod memory;
mod scheduler;
mod syscall;

use bootloader_api::{entry_point, BootInfo, BootloaderConfig};
use core::panic::PanicInfo;

pub static BOOTLOADER_CONFIG: BootloaderConfig = {
    let mut config = BootloaderConfig::new_default();
    config.mappings.physical_memory = Some(bootloader_api::config::Mapping::Dynamic);
    config.kernel_stack_size = 256 * 1024;
    config
};

entry_point!(kernel_main, config = &BOOTLOADER_CONFIG);

fn kernel_main(boot_info: &'static mut BootInfo) -> ! {
    // Phase 1: Serial console (debug)
    drivers::serial::init();
    kprintln!("╔══════════════════════════════════════════════╗");
    kprintln!("║  JARVIS Microkernel v0.1.0                   ║");
    kprintln!("║  Autonomous Intelligence Operating System     ║");
    kprintln!("╚══════════════════════════════════════════════╝");
    kprintln!();

    // Phase 2: Framebuffer display
    kprintln!("[BOOT] Initializing framebuffer display...");
    if let Some(fb) = boot_info.framebuffer.as_mut() {
        let info = fb.info();
        kprintln!("[BOOT] Framebuffer: {}x{} {:?} (stride: {}, bpp: {})",
            info.width, info.height, info.pixel_format, info.stride, info.bytes_per_pixel);
        drivers::framebuffer::init(fb);
        kprintln!("[BOOT] Framebuffer initialized");
    } else {
        kprintln!("[BOOT] WARNING: No framebuffer available");
    }

    // Display boot banner on screen
    fbprintln!("J.A.R.V.I.S. Microkernel v0.1.0");
    fbprintln!("Autonomous Intelligence Operating System");
    fbprintln!("────────────────────────────────────────────");
    fbprintln!();

    // Phase 3: Memory management
    kprintln!("[BOOT] Initializing memory management...");
    fbprintln!("[BOOT] Memory management...");
    let phys_mem_offset = boot_info
        .physical_memory_offset
        .into_option()
        .expect("physical memory mapping required");
    let memory_regions = &boot_info.memory_regions;
    memory::init(phys_mem_offset, memory_regions);
    kprintln!("[BOOT] Memory subsystem online");
    fbprintln!("[  OK] Memory subsystem online");

    // Phase 4: Interrupts
    kprintln!("[BOOT] Initializing interrupts...");
    fbprintln!("[BOOT] Interrupt handling...");
    interrupts::init();
    kprintln!("[BOOT] Interrupts online");
    fbprintln!("[  OK] Interrupts online");

    // Phase 5: Scheduler
    kprintln!("[BOOT] Initializing task scheduler...");
    fbprintln!("[BOOT] Task scheduler...");
    scheduler::init();
    kprintln!("[BOOT] Scheduler online");
    fbprintln!("[  OK] Scheduler online");

    // Phase 6: IPC
    kprintln!("[BOOT] Initializing IPC...");
    fbprintln!("[BOOT] IPC message bus...");
    ipc::init();
    kprintln!("[BOOT] IPC online");
    fbprintln!("[  OK] IPC online");

    // Phase 7: JARVIS subsystems
    kprintln!("[BOOT] Initializing JARVIS subsystems...");
    fbprintln!("[BOOT] JARVIS subsystems...");
    jarvis::init();
    kprintln!("[BOOT] JARVIS kernel online");
    fbprintln!("[  OK] JARVIS kernel online");

    fbprintln!();
    fbprintln!("All systems nominal.");
    fbprintln!("Good morning, Ulrich.");
    fbprintln!();
    fbprintln!("jarvis> ");

    kprintln!();
    kprintln!("[JARVIS] All systems nominal. Entering main loop.");

    x86_64::instructions::interrupts::enable();

    loop {
        x86_64::instructions::hlt();
    }
}

#[panic_handler]
fn panic(info: &PanicInfo) -> ! {
    kprintln!();
    kprintln!("╔══════════════════════════════════════════════╗");
    kprintln!("║  KERNEL PANIC                                ║");
    kprintln!("╚══════════════════════════════════════════════╝");
    kprintln!("{}", info);

    fbprintln!();
    fbprintln!("*** KERNEL PANIC ***");
    fbprintln!("{}", info);

    loop {
        x86_64::instructions::hlt();
    }
}

/// Print to framebuffer (screen)
#[macro_export]
macro_rules! fbprint {
    ($($arg:tt)*) => ($crate::drivers::framebuffer::_print(format_args!($($arg)*)));
}

/// Print line to framebuffer (screen)
#[macro_export]
macro_rules! fbprintln {
    () => ($crate::fbprint!("\n"));
    ($($arg:tt)*) => ($crate::fbprint!("{}\n", format_args!($($arg)*)));
}