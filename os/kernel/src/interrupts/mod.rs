//! JARVIS Interrupt Handling
//!
//! Sets up the IDT (Interrupt Descriptor Table), PIC (Programmable Interrupt Controller),
//! timer (PIT), and keyboard interrupts.

use crate::kprintln;
use pic8259::ChainedPics;
use spin::Mutex;
use x86_64::structures::idt::{InterruptDescriptorTable, InterruptStackFrame, PageFaultErrorCode};

/// PIC offsets — remap hardware interrupts away from CPU exceptions
const PIC_1_OFFSET: u8 = 32;
const PIC_2_OFFSET: u8 = PIC_1_OFFSET + 8;

/// Hardware interrupt numbers
#[derive(Debug, Clone, Copy)]
#[repr(u8)]
pub enum InterruptIndex {
    Timer = PIC_1_OFFSET,
    Keyboard = PIC_1_OFFSET + 1,
}

pub static PICS: Mutex<ChainedPics> =
    Mutex::new(unsafe { ChainedPics::new(PIC_1_OFFSET, PIC_2_OFFSET) });

static mut IDT: InterruptDescriptorTable = InterruptDescriptorTable::new();

/// Tick counter — incremented by the timer interrupt
static TICKS: core::sync::atomic::AtomicU64 = core::sync::atomic::AtomicU64::new(0);

pub fn init() {
    // Set up IDT entries
    unsafe {
        // CPU exceptions
        IDT.breakpoint.set_handler_fn(breakpoint_handler);
        IDT.double_fault.set_handler_fn(double_fault_handler);
        IDT.page_fault.set_handler_fn(page_fault_handler);
        IDT.general_protection_fault
            .set_handler_fn(general_protection_handler);

        // Hardware interrupts
        IDT[InterruptIndex::Timer as u8].set_handler_fn(timer_handler);
        IDT[InterruptIndex::Keyboard as u8].set_handler_fn(keyboard_handler);

        // Load IDT
        IDT.load_unsafe();
    }

    // Initialize and enable PICs
    unsafe {
        PICS.lock().initialize();
    }

    // Initialize keyboard driver
    crate::drivers::keyboard::init();

    kprintln!("[INT] IDT loaded, PICs initialized");
}

/// Get current tick count (increments ~18.2 times/sec with default PIT)
pub fn ticks() -> u64 {
    TICKS.load(core::sync::atomic::Ordering::Relaxed)
}

// ── Exception Handlers ──────────────────────────────────────────

extern "x86-interrupt" fn breakpoint_handler(stack_frame: InterruptStackFrame) {
    kprintln!("[INT] BREAKPOINT\n{:#?}", stack_frame);
}

extern "x86-interrupt" fn double_fault_handler(
    stack_frame: InterruptStackFrame,
    _error_code: u64,
) -> ! {
    kprintln!("[INT] DOUBLE FAULT\n{:#?}", stack_frame);
    panic!("DOUBLE FAULT");
}

extern "x86-interrupt" fn page_fault_handler(
    stack_frame: InterruptStackFrame,
    error_code: PageFaultErrorCode,
) {
    use x86_64::registers::control::Cr2;
    kprintln!("[INT] PAGE FAULT");
    kprintln!("  Accessed Address: {:?}", Cr2::read());
    kprintln!("  Error Code: {:?}", error_code);
    kprintln!("{:#?}", stack_frame);
    panic!("PAGE FAULT");
}

extern "x86-interrupt" fn general_protection_handler(
    stack_frame: InterruptStackFrame,
    error_code: u64,
) {
    kprintln!("[INT] GENERAL PROTECTION FAULT (code: {})", error_code);
    kprintln!("{:#?}", stack_frame);
    panic!("GENERAL PROTECTION FAULT");
}

// ─��� Hardware Interrupt Handlers ─────────────────────────────────

extern "x86-interrupt" fn timer_handler(_stack_frame: InterruptStackFrame) {
    TICKS.fetch_add(1, core::sync::atomic::Ordering::Relaxed);

    // Notify the scheduler on every tick
    crate::scheduler::on_tick();

    // Send End of Interrupt to PIC
    unsafe {
        PICS.lock()
            .notify_end_of_interrupt(InterruptIndex::Timer as u8);
    }
}

extern "x86-interrupt" fn keyboard_handler(_stack_frame: InterruptStackFrame) {
    let scancode = crate::drivers::keyboard::read_scancode();
    crate::drivers::keyboard::handle_scancode(scancode);

    unsafe {
        PICS.lock()
            .notify_end_of_interrupt(InterruptIndex::Keyboard as u8);
    }
}
