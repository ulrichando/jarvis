"use client";

import { usePathname } from "next/navigation";
import { PreviewToggle } from "./preview";

export function TopBar() {
  const pathname = usePathname();
  // Workbench / Code / Design each render their own full toolbar.
  // SidebarToggle is injected per-page (in each header) so a stacked
  // empty TopBar isn't sitting above page content.
  if (
    pathname.startsWith("/workbench") ||
    pathname.startsWith("/code") ||
    pathname.startsWith("/design") ||
    pathname.startsWith("/computer-use") ||
    pathname.startsWith("/chat")
  )
    return null;
  return (
    <header className="flex h-12 shrink-0 items-center justify-between px-3">
      <div className="pl-10 md:pl-2" />
      <div className="flex items-center gap-1">
        <PreviewToggle />
      </div>
    </header>
  );
}
