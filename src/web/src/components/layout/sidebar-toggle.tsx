"use client";

import { PanelLeftOpen } from "lucide-react";
import { useUI } from "@/stores/ui";

/**
 * Shared sidebar-open button. Renders only when the global app sidebar
 * is collapsed; clicking opens it. Matches the icon + behavior the
 * Chat component shows so the affordance is consistent across every
 * top-level tab (Chat, Chats, Search, Code, Workbench, Design, etc.).
 */
export function SidebarToggle({ className }: { className?: string }) {
  const { sidebarOpen, toggleSidebar } = useUI();
  if (sidebarOpen) return null;
  return (
    <button
      type="button"
      onClick={toggleSidebar}
      aria-label="Open sidebar"
      title="Open sidebar"
      className={
        "flex shrink-0 items-center justify-center px-2.5 text-muted-foreground hover:text-foreground hover:bg-muted/40 transition-colors" +
        (className ? " " + className : "")
      }
    >
      <PanelLeftOpen className="size-4" />
    </button>
  );
}
