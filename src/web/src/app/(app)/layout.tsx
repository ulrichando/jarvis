"use client";

import { usePathname } from "next/navigation";
import { Sidebar } from "@/components/layout/sidebar";
import { useSettings } from "@/hooks/use-settings";

export default function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  // Settings → General → Appearance rides these data attributes: globals.css
  // maps them to --chat-fs / --chat-gap etc., consumed by chat text + thread
  // spacing (and the Appearance preview in Settings). At the layout root so
  // EVERY surface under the shell reacts, not just a mounted chat thread.
  const { data: settings } = useSettings();
  const appearanceAttrs = {
    "data-chat-font": settings?.appearance?.fontSize ?? "md",
    "data-chat-density": settings?.appearance?.density ?? "cozy",
  } as const;
  // /code presents as standalone (its own full-screen sidebar, like
  // claude.ai/code) — render WITHOUT the app shell (Sidebar).
  // Additive: every other route keeps the normal shell unchanged.
  if (pathname?.startsWith("/code")) {
    return (
      <div className="h-screen w-full overflow-hidden" {...appearanceAttrs}>
        {children}
      </div>
    );
  }
  return (
    <div
      className="relative flex h-screen w-full overflow-hidden"
      {...appearanceAttrs}
    >
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <main className="flex-1 overflow-hidden">{children}</main>
      </div>
    </div>
  );
}
