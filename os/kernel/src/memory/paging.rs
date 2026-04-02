//! Page Table Management
//!
//! Virtual memory mapping for the JARVIS kernel.
//! Uses the x86_64 4-level page tables set up by the bootloader.

use spin::Mutex;
use x86_64::registers::control::Cr3;
use x86_64::structures::paging::{
    Mapper, OffsetPageTable, Page, PageTableFlags, PhysFrame, Size4KiB,
};
use x86_64::{PhysAddr, VirtAddr};

use super::frame::JarvisFrameAllocator;

static PAGE_TABLE: Mutex<Option<OffsetPageTable<'static>>> = Mutex::new(None);
static mut PHYS_OFFSET: u64 = 0;

/// Initialize paging with the physical memory offset from the bootloader
pub fn init(physical_memory_offset: VirtAddr) {
    unsafe {
        PHYS_OFFSET = physical_memory_offset.as_u64();
    }

    let level_4_table = unsafe {
        let (frame, _) = Cr3::read();
        let phys = frame.start_address();
        let virt = physical_memory_offset + phys.as_u64();
        let page_table_ptr: *mut x86_64::structures::paging::PageTable = virt.as_mut_ptr();
        &mut *page_table_ptr
    };

    let mapper = unsafe { OffsetPageTable::new(level_4_table, physical_memory_offset) };
    *PAGE_TABLE.lock() = Some(mapper);
}

/// Map a virtual page to a physical frame
pub fn map_page(page: Page<Size4KiB>, frame: PhysFrame<Size4KiB>, flags: PageTableFlags) {
    let mut mapper = PAGE_TABLE.lock();
    let mut frame_allocator = JarvisFrameAllocator;

    if let Some(ref mut mapper) = *mapper {
        unsafe {
            let result = mapper.map_to(page, frame, flags, &mut frame_allocator);
            if let Ok(flush) = result {
                flush.flush();
            }
        }
    }
}

/// Translate a virtual address to physical
pub fn translate_addr(addr: VirtAddr) -> Option<PhysAddr> {
    use x86_64::structures::paging::Translate;
    let mapper = PAGE_TABLE.lock();
    mapper.as_ref().and_then(|m| m.translate_addr(addr))
}
