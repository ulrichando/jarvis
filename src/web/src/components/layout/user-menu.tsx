"use client";

import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import { Settings, LogOut, LifeBuoy, ChevronDown, Loader2 } from "lucide-react";
import { useSession, signOut } from "@/lib/auth-client";
import { useUI } from "@/stores/ui";

function initials(name: string): string {
  return name
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("") || "U";
}

export function UserMenu({ fallbackName = "You" }: { fallbackName?: string }) {
  const router = useRouter();
  const { data } = useSession();
  const openSettings = useUI((s) => s.openSettings);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  // useSession() resolves the cached session synchronously on the client but
  // is empty during SSR, so reading it on the first render mismatches the
  // server-rendered fallback ("You"→Y vs "Ulrich"→U → hydration error). Gate
  // the session-derived values behind a mount flag: first client render uses
  // the fallback (matching the server), then we swap to the real user.
  const [mounted, setMounted] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => setMounted(true), []);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [open]);

  const name = (mounted && data?.user?.name) || fallbackName;
  const email = (mounted && data?.user?.email) || "local";

  const logout = async () => {
    setBusy(true);
    await signOut().catch(() => {});
    router.push("/login");
    router.refresh();
  };

  const item =
    "flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-left text-[13px] text-foreground/90 hover:bg-accent/50 transition-colors";

  return (
    <div className="relative" ref={ref}>
      {open && (
        <div className="absolute bottom-full left-0 z-50 mb-2 w-full min-w-[230px] rounded-xl border border-border bg-card p-1 shadow-xl">
          <div className="truncate px-2.5 py-1.5 text-[12px] text-muted-foreground">{email}</div>
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              openSettings("general");
            }}
            className={item}
          >
            <Settings className="size-3.5 text-muted-foreground" />
            <span className="flex-1">Settings</span>
            <span className="text-[11px] text-muted-foreground/60">⌘ ⇧ ,</span>
          </button>
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              openSettings("about");
            }}
            className={item}
          >
            <LifeBuoy className="size-3.5 text-muted-foreground" />
            <span className="flex-1">Get help</span>
          </button>
          <div className="my-1 border-t border-border/50" />
          <button type="button" onClick={logout} disabled={busy} className={item}>
            {busy ? <Loader2 className="size-3.5 animate-spin" /> : <LogOut className="size-3.5 text-muted-foreground" />}
            <span className="flex-1">Log out</span>
          </button>
        </div>
      )}
      {/* claude.ai-style footer row: avatar + name + chevron, nothing else
          (email lives in the menu above; no plan label by request). */}
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-sidebar-accent/60"
      >
        <div className="flex size-6 shrink-0 items-center justify-center rounded-full bg-primary/20 font-mono text-[10px] font-semibold tracking-wider text-primary">
          {initials(name)}
        </div>
        <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-sidebar-foreground">
          {name}
        </span>
        <ChevronDown className="size-3.5 shrink-0 text-sidebar-foreground/50" />
      </button>
    </div>
  );
}
