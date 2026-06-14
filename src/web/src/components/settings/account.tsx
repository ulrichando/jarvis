"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Copy, Check } from "lucide-react";
import { Button } from "@/components/ui/button";

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
  const [resetting, setResetting] = useState(false);
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
        <div className="divide-y divide-border/60">
          <div className="flex items-center justify-between py-3.5">
            <div>
              <p className="text-[14px] font-medium">Settings file</p>
              <p className="mt-0.5 font-mono text-[12px] text-muted-foreground">
                .jarvis/settings.json
              </p>
            </div>
            <CopyButton value=".jarvis/settings.json" />
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
