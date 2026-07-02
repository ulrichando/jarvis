"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Copy, Check, LogOut, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useSession, signOut } from "@/lib/auth-client";

// Initials for the avatar fallback (mirrors layout/user-menu.tsx).
function initials(name: string): string {
  return (
    name
      .trim()
      .split(/\s+/)
      .slice(0, 2)
      .map((w) => w[0]?.toUpperCase() ?? "")
      .join("") || "U"
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-4">
      <h2 className="text-[17px] font-semibold">{children}</h2>
      <div className="mt-2 border-t border-border/60" />
    </div>
  );
}

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard.writeText(value).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <button
      type="button"
      onClick={copy}
      className="ml-2 inline-flex items-center text-muted-foreground hover:text-foreground transition-colors"
      aria-label="Copy"
    >
      {copied ? <Check className="size-3.5 text-primary" /> : <Copy className="size-3.5" />}
    </button>
  );
}

const INSTANCE_ID = "jarvis-local";

export function AccountSection() {
  const qc = useQueryClient();
  const router = useRouter();
  const { data: session } = useSession();
  const [resetting, setResetting] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  // useSession() is empty during SSR and resolves on the client — gate the
  // session-derived text behind a mount flag so the first client render matches
  // the server (avoids a hydration mismatch). Same pattern as user-menu.tsx.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const name = (mounted && session?.user?.name) || "You";
  const email = (mounted && session?.user?.email) || "Local account";

  const logout = async () => {
    if (signingOut) return;
    setSigningOut(true);
    await signOut().catch(() => {});
    router.push("/login");
    router.refresh();
  };
  const resetSettings = async () => {
    if (resetting) return;
    if (
      !window.confirm(
        "Reset all settings to defaults? Your API keys and conversations are kept.",
      )
    ) {
      return;
    }
    setResetting(true);
    try {
      const r = await fetch("/api/settings", { method: "DELETE" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await qc.invalidateQueries({ queryKey: ["settings"] });
      toast.success("Settings reset to defaults");
    } catch (e) {
      toast.error(`Reset failed: ${(e as Error).message}`);
    } finally {
      setResetting(false);
    }
  };

  return (
    <div className="space-y-10">
      <section>
        <SectionTitle>Account</SectionTitle>
        {/* Signed-in identity + sign out — the core of an Account page, which
            this section was missing (session was available via useSession all
            along). */}
        <div className="mb-4 flex items-center gap-3 rounded-xl border border-border/60 bg-card/40 px-4 py-3.5">
          <div className="flex size-10 shrink-0 items-center justify-center rounded-full bg-primary/20 font-mono text-[13px] font-semibold tracking-wider text-primary">
            {initials(name)}
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-[14px] font-medium">{name}</p>
            <p className="truncate text-[12.5px] text-muted-foreground">{email}</p>
          </div>
          <Button variant="outline" size="sm" className="gap-1.5" onClick={logout} disabled={signingOut}>
            {signingOut ? <Loader2 className="size-3.5 animate-spin" /> : <LogOut className="size-3.5" />}
            Sign out
          </Button>
        </div>
        <div className="divide-y divide-border/60">
          <div className="flex items-center justify-between py-3.5">
            <div>
              <p className="text-[14px] font-medium">Settings file</p>
              <p className="mt-0.5 font-mono text-[12px] text-muted-foreground">
                ~/.jarvis/settings.json
              </p>
            </div>
            <CopyButton value="~/.jarvis/settings.json" />
          </div>
          <div className="flex items-center justify-between py-3.5">
            <div>
              <p className="text-[14px] font-medium">Instance ID</p>
              <p className="mt-0.5 font-mono text-[12px] text-muted-foreground">
                {INSTANCE_ID}
              </p>
            </div>
            <CopyButton value={INSTANCE_ID} />
          </div>
        </div>
      </section>

      <section>
        <SectionTitle>Danger zone</SectionTitle>
        <div className="divide-y divide-border/60">
          <div className="flex items-center justify-between py-3.5">
            <div>
              <p className="text-[14px] font-medium">Reset settings to defaults</p>
              <p className="mt-0.5 text-[13px] text-muted-foreground">
                Clears all settings. API keys and conversations are unaffected.
              </p>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={resetSettings}
              disabled={resetting}
            >
              {resetting ? "Resetting…" : "Reset"}
            </Button>
          </div>
        </div>
      </section>
    </div>
  );
}
