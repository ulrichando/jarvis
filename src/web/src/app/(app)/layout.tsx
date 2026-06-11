"use client";

import { usePathname } from "next/navigation";
import { Sidebar } from "@/components/layout/sidebar";
import { TopBar } from "@/components/layout/topbar";
import { PreviewPanel } from "@/components/layout/preview";

export default function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  // /code presents as standalone (its own full-screen sidebar, like
  // claude.ai/code) — render WITHOUT the app shell (Sidebar/TopBar/Preview).
  // Additive: every other route keeps the normal shell unchanged.
  if (pathname?.startsWith("/code")) {
    return <div className="h-screen w-full overflow-hidden">{children}</div>;
  }
  return (
    <div className="relative flex h-screen w-full overflow-hidden">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <main className="flex-1 overflow-hidden">{children}</main>
      </div>
      <PreviewPanel />
    </div>
  );
}
