"use client";

import { useState } from "react";
import { toast } from "sonner";
import { ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useConversations } from "@/hooks/use-conversations";

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-4">
      <h2 className="text-[17px] font-semibold">{children}</h2>
      <div className="mt-2 border-t border-border/60" />
    </div>
  );
}

export function PrivacySection() {
  const { data: conversations } = useConversations();
  const [exporting, setExporting] = useState(false);

  const exportAll = async () => {
    if (!conversations || conversations.length === 0) {
      toast.error("No conversations to export");
      return;
    }
    setExporting(true);
    try {
      const full = await Promise.all(
        conversations.map((c) =>
          fetch(`/api/conversations/${c.id}`)
            .then((r) => r.json())
            .catch(() => null),
        ),
      );
      const blob = new Blob([JSON.stringify(full, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `jarvis-export-${new Date().toISOString().split("T")[0]}.json`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success(`Exported ${conversations.length} chats`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Export failed");
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="space-y-10">
      <section>
        <div className="mb-6 flex items-start gap-3 rounded-xl border border-border/60 bg-card/40 p-4">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10">
            <ShieldCheck className="size-4.5 text-primary" />
          </div>
          <div>
            <p className="text-[14px] font-semibold">Privacy</p>
            <p className="mt-0.5 text-[13px] text-muted-foreground">
              Jarvis stores all data locally on your machine. Your conversations
              and settings are never shared with anyone.
            </p>
          </div>
        </div>
      </section>

      <section>
        <SectionTitle>Privacy settings</SectionTitle>
        <div className="divide-y divide-border/60">
          <div className="flex items-center justify-between py-3.5">
            <div>
              <p className="text-[14px] font-medium">Export data</p>
              <p className="mt-0.5 text-[13px] text-muted-foreground">
                Download all your conversations as a JSON file.
              </p>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={exportAll}
              disabled={exporting || !conversations || conversations.length === 0}
            >
              {exporting ? "Exporting…" : "Export data"}
            </Button>
          </div>
          <div className="flex items-center justify-between py-3.5">
            <div>
              <p className="text-[14px] font-medium">Data storage</p>
              <p className="mt-0.5 text-[13px] text-muted-foreground">
                All data is stored in <span className="font-mono text-[12px]">~/.jarvis/</span> on your machine.
              </p>
            </div>
            <span className="rounded-full border border-border/60 px-2.5 py-1 font-mono text-[11px] text-muted-foreground">
              local
            </span>
          </div>
          <div className="flex items-center justify-between py-3.5">
            <div>
              <p className="text-[14px] font-medium">Telemetry</p>
              <p className="mt-0.5 text-[13px] text-muted-foreground">
                No usage data is collected. Jarvis runs entirely offline.
              </p>
            </div>
            <span className="rounded-full border border-primary/40 bg-primary/10 px-2.5 py-1 font-mono text-[11px] text-primary">
              disabled
            </span>
          </div>
        </div>
      </section>
    </div>
  );
}
