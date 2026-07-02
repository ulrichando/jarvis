"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { Sidebar } from "@/components/layout/sidebar";
import { useSettings } from "@/hooks/use-settings";

// Font size → the <html> root font-size. Every rem-based Tailwind utility
// (text AND spacing) is relative to the root, so this scales the WHOLE app,
// not just chat. Must be on documentElement: rem ignores font-size set on any
// inner element. md = the browser default (16px).
const ROOT_FONT_PX: Record<string, string> = { sm: "15px", md: "16px", lg: "18px" };

export default function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  // Settings → General → Appearance. These data attributes are applied at the
  // app-shell root so EVERY surface reacts (globals.css maps
  // data-chat-density → --spacing, which every Tailwind padding/gap/margin
  // resolves, plus the chat-specific --chat-* vars). Font size additionally
  // rides the <html> root font-size below.
  const { data: settings } = useSettings();
  const fontSize = settings?.appearance?.fontSize ?? "md";
  const appearanceAttrs = {
    "data-chat-font": fontSize,
    "data-chat-density": settings?.appearance?.density ?? "cozy",
  } as const;

  // Drive the <html> root font-size app-wide from the font setting. Reset on
  // unmount so leaving the app shell restores the browser default.
  useEffect(() => {
    const px = ROOT_FONT_PX[fontSize] ?? ROOT_FONT_PX.md;
    document.documentElement.style.fontSize = px;
    return () => {
      document.documentElement.style.fontSize = "";
    };
  }, [fontSize]);
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
