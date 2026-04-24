"use client";

import { PreviewToggle } from "./preview";

export function TopBar() {
  return (
    <header className="flex h-12 shrink-0 items-center justify-between px-3">
      <div className="pl-10 md:pl-2" />
      <div className="flex items-center gap-1">
        <PreviewToggle />
      </div>
    </header>
  );
}
