"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Download, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { SettingsSection } from "./field";
import { useConversations } from "@/hooks/use-conversations";
import {
  fetchConversationsForExport,
  triggerJsonDownload,
} from "@/lib/export-conversations";

export function DataSection() {
  const { data: conversations } = useConversations();
  const qc = useQueryClient();
  const [exporting, setExporting] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const deleteAll = async () => {
    if (deleting || !conversations || conversations.length === 0) return;
    if (
      !window.confirm(
        `Delete all ${conversations.length} conversation${conversations.length === 1 ? "" : "s"}? This can't be undone. Settings are kept.`,
      )
    ) {
      return;
    }
    setDeleting(true);
    try {
      // fetch() resolves even on HTTP 5xx, so check each .ok — otherwise a
      // failed server-side delete still reported success.
      const oks = await Promise.all(
        conversations.map((c) =>
          fetch(`/api/conversations/${c.id}`, { method: "DELETE" })
            .then((r) => r.ok)
            .catch(() => false),
        ),
      );
      await qc.invalidateQueries({ queryKey: ["conversations"] });
      const failed = oks.filter((ok) => !ok).length;
      if (failed > 0) {
        toast.error(`Deleted ${oks.length - failed} of ${oks.length} — ${failed} failed`);
      } else {
        toast.success("All conversations deleted");
      }
    } catch (e) {
      toast.error(`Delete failed: ${(e as Error).message}`);
    } finally {
      setDeleting(false);
    }
  };

  const exportAll = async () => {
    if (!conversations || conversations.length === 0) {
      toast.error("No conversations to export");
      return;
    }
    setExporting(true);
    try {
      const { data, requested, failed } = await fetchConversationsForExport(
        conversations.map((c) => c.id),
      );
      if (data.length === 0) {
        toast.error("Export failed — no conversations could be fetched");
        return;
      }
      triggerJsonDownload(
        data,
        `jarvis-export-${new Date().toISOString().split("T")[0]}.json`,
      );
      if (failed > 0) {
        toast.warning(`Exported ${data.length} of ${requested} chats — ${failed} could not be fetched`);
      } else {
        toast.success(`Exported ${data.length} chats`);
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Export failed");
    } finally {
      setExporting(false);
    }
  };

  return (
    <>
      <SettingsSection
        title="Conversations"
        description={
          conversations
            ? `${conversations.length} conversation${conversations.length === 1 ? "" : "s"} stored locally.`
            : "No persistence configured."
        }
      >
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium">Export all chats</p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Downloads a JSON dump of every conversation and message.
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={exportAll}
            disabled={exporting || !conversations || conversations.length === 0}
          >
            <Download className="size-3.5" />
            {exporting ? "Exporting…" : "Export"}
          </Button>
        </div>
      </SettingsSection>

      <SettingsSection title="Storage">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium">Settings file</p>
            <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">
              ~/.jarvis/settings.json
            </p>
          </div>
          <span className="rounded-full border border-border/60 px-2 py-0.5 font-mono text-[10px] text-muted-foreground">
            local
          </span>
        </div>
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium">Chat history</p>
            <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">
              DATABASE_URL (Postgres) or disabled
            </p>
          </div>
          <span className="rounded-full border border-border/60 px-2 py-0.5 font-mono text-[10px] text-muted-foreground">
            {conversations ? "active" : "disabled"}
          </span>
        </div>
      </SettingsSection>

      <SettingsSection
        title="Danger zone"
        description="Irreversible. You'll be asked to confirm first."
      >
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium text-destructive">
              Delete all conversations
            </p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Wipes every chat and message. Settings remain.
            </p>
          </div>
          <Button
            variant="destructive"
            size="sm"
            onClick={deleteAll}
            disabled={deleting || !conversations || conversations.length === 0}
          >
            <Trash2 className="size-3.5" />
            {deleting ? "Deleting…" : "Delete all"}
          </Button>
        </div>
      </SettingsSection>
    </>
  );
}
