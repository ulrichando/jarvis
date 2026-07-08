"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Eye, EyeOff, Plus, X } from "lucide-react";
import { toast } from "sonner";
import { type WorkspaceMeta, patchWorkspace, Section } from "./shared";

// ── Environment variables ──────────────────────────────────────────────

export function EnvVarsSection({
  ws,
  workspaceId,
  revealKeys,
  onToggleReveal,
  onChanged,
}: {
  ws: WorkspaceMeta | null;
  workspaceId: string;
  revealKeys: string[];
  onToggleReveal: (k: string) => void;
  onChanged: () => void;
}) {
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const env = ws?.envVars ?? {};
  const keys = Object.keys(env).sort();

  const save = useMutation({
    // The server MERGES envVars and applies removeEnvKeys explicitly, so
    // we only ever send the keys that actually changed. Masked secrets we
    // can't see are preserved server-side instead of being silently
    // dropped (the old "send back everything we have plaintext for"
    // approach wiped every unrevealed secret on any edit).
    mutationFn: (patch: {
      envVars?: Record<string, string>;
      removeEnvKeys?: string[];
    }) => patchWorkspace(workspaceId, patch),
    onSuccess: () => {
      toast.success("Environment variables saved", {
        description:
          "Restart the sandbox from the Runtime section for changes to take effect.",
      });
      onChanged();
    },
    onError: (err: Error) => toast.error(`Save failed: ${err.message}`),
  });

  const addVar = () => {
    const key = newKey.trim().toUpperCase();
    if (!key || !/^[A-Z_][A-Z0-9_]*$/.test(key)) {
      toast.error("Invalid key", {
        description: "Use uppercase letters, digits, and underscores only.",
      });
      return;
    }
    if (newValue.length > 4096) {
      toast.error("Value too long", {
        description: "Environment variable values are capped at 4096 characters.",
      });
      return;
    }
    save.mutate({ envVars: { [key]: newValue } });
    setNewKey("");
    setNewValue("");
  };

  const removeVar = (key: string) => {
    save.mutate({ removeEnvKeys: [key] });
  };

  return (
    <Section
      title="Environment variables"
      hint="Injected into the sandbox container on start. Secret-class values (KEY/TOKEN/SECRET/PASSWORD/DSN/URL) are masked by default — click the eye to reveal. After changing, restart the sandbox to pick up new values."
    >
      <div className="space-y-1.5">
        {keys.length === 0 && (
          <div className="rounded-md border border-dashed border-border/60 px-3 py-3 text-center text-[12px] text-muted-foreground">
            No environment variables yet.
          </div>
        )}
        {keys.map((k) => {
          const e = env[k];
          const revealed = revealKeys.includes(k) || !e.masked;
          return (
            <div
              key={k}
              className="flex items-center gap-2 rounded-md border border-border/40 px-2.5 py-1.5"
            >
              <span className="w-44 truncate font-mono text-[11.5px] font-medium">
                {k}
              </span>
              <span className="flex-1 truncate font-mono text-[11.5px] text-foreground/80">
                {revealed ? e.value : "••••••••"}
              </span>
              {e.masked && (
                <button
                  type="button"
                  onClick={() => onToggleReveal(k)}
                  className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
                  aria-label={revealed ? "Hide value" : "Reveal value"}
                  title={revealed ? "Hide value" : "Reveal value"}
                >
                  {revealed ? (
                    <EyeOff className="size-3.5" />
                  ) : (
                    <Eye className="size-3.5" />
                  )}
                </button>
              )}
              <button
                type="button"
                onClick={() => removeVar(k)}
                className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                aria-label="Remove"
                title="Remove"
              >
                <X className="size-3.5" />
              </button>
            </div>
          );
        })}
      </div>
      <div className="mt-3 flex items-center gap-2">
        <input
          value={newKey}
          onChange={(e) => setNewKey(e.target.value)}
          placeholder="KEY"
          className="w-44 rounded-md border border-border bg-background px-2 py-1.5 font-mono text-[11.5px] outline-none focus:border-primary"
        />
        <input
          value={newValue}
          onChange={(e) => setNewValue(e.target.value)}
          placeholder="value"
          className="flex-1 rounded-md border border-border bg-background px-2 py-1.5 font-mono text-[11.5px] outline-none focus:border-primary"
        />
        <button
          type="button"
          onClick={addVar}
          disabled={save.isPending || !newKey.trim()}
          className="flex items-center gap-1 rounded-md border border-border px-2 py-1.5 text-[11.5px] hover:bg-accent disabled:opacity-50"
        >
          <Plus className="size-3.5" />
          Add
        </button>
      </div>
    </Section>
  );
}
