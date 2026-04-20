// GDT — Global Descriptor Table.
//
// The CPU, after long-mode boot, still consults segment descriptors for a
// handful of things (TSS loading, code/data CS). We install a minimal 64-bit
// GDT with one code segment and a TSS entry so our interrupt handlers have a
// known-good stack via the IST (Interrupt Stack Table) — mandatory for double
// faults, otherwise a bad stack pointer causes triple-fault reboot.

use lazy_static::lazy_static;
use x86_64::instructions::segmentation::{Segment, CS};
use x86_64::instructions::tables::load_tss;
use x86_64::structures::gdt::{Descriptor, GlobalDescriptorTable, SegmentSelector};
use x86_64::structures::tss::TaskStateSegment;
use x86_64::VirtAddr;

/// Dedicated stack for the double-fault handler so a stack-overflow
/// triggering a double-fault doesn't recursively double-fault.
pub const DOUBLE_FAULT_IST_INDEX: u16 = 0;

lazy_static! {
    static ref TSS: TaskStateSegment = {
        let mut tss = TaskStateSegment::new();
        tss.interrupt_stack_table[DOUBLE_FAULT_IST_INDEX as usize] = {
            const STACK_SIZE: usize = 4096 * 5;
            static mut STACK: [u8; STACK_SIZE] = [0; STACK_SIZE];
            let stack_start = VirtAddr::from_ptr(unsafe { &raw const STACK });
            stack_start + STACK_SIZE as u64
        };
        tss
    };
}

struct Selectors {
    code: SegmentSelector,
    tss: SegmentSelector,
}

lazy_static! {
    static ref GDT: (GlobalDescriptorTable, Selectors) = {
        let mut gdt = GlobalDescriptorTable::new();
        let code = gdt.add_entry(Descriptor::kernel_code_segment());
        let tss = gdt.add_entry(Descriptor::tss_segment(&TSS));
        (gdt, Selectors { code, tss })
    };
}

pub fn init() {
    GDT.0.load();
    unsafe {
        CS::set_reg(GDT.1.code);
        load_tss(GDT.1.tss);
    }
}
