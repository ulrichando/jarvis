"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Check, Eye, EyeOff, GitBranch, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SettingsSection } from "./field";
import { useSettings, useUpdateSettings } from "@/hooks/use-settings";

export function IntegrationsSection() {
  const { data } = useSettings();
  if (!data) return <p className="text-sm text-muted-foreground">Loading…</p>;

  return (
    <SettingsSection
      description="Tokens stay local in ~/.jarvis/settings.json. Used to push workspaces to GitHub from the Workbench's History tab."
    >
      <GitHubRow />
    </SettingsSection>
  );
}

function GitHubRow() {
  const { data } = useSettings();
  const update = useUpdateSettings();
  const stored = data?.integrations?.github;

  const [token, setToken] = useState("");
  const [defaultOwner, setDefaultOwner] = useState(
    stored?.defaultOwner ?? "",
  );
  const [show, setShow] = useState(false);

  const hasToken = stored?.hasToken;

  const save = async () => {
    if (!token.trim() && defaultOwner.trim() === (stored?.defaultOwner ?? "")) {
      return;
    }
    try {
      await update.mutateAsync({
        integrations: {
          github: {
            ...(token.trim() ? { token: token.trim() } : {}),
            defaultOwner: defaultOwner.trim() || null,
          },
        },
      });
      setToken("");
      toast.success("GitHub saved");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Save failed");
    }
  };

  const clear = async () => {
    try {
      await update.mutateAsync({
        integrations: { github: { token: null } },
      });
      setToken("");
      toast.success("GitHub token removed");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Remove failed");
    }
  };

  return (
    <div className="rounded-md border border-border/60 bg-background/40 p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <GitBranch className="size-3.5 text-muted-foreground" />
          <span className="text-sm font-medium">GitHub</span>
          {hasToken ? (
            <span className="flex items-center gap-1 rounded-full border border-primary/40 bg-primary/10 px-2 py-0.5 font-mono text-[10px] text-primary">
              <Check className="size-2.5" />
              {stored?.tokenPreview}
            </span>
          ) : (
            <span className="rounded-full border border-border/60 px-2 py-0.5 font-mono text-[10px] text-muted-foreground">
              not set
            </span>
          )}
        </div>
        <a
          href="https://github.com/settings/tokens"
          target="_blank"
          rel="noreferrer"
          className="text-[11px] text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
        >
          Generate token →
        </a>
      </div>

      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <Input
            type={show ? "text" : "password"}
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder={
              hasToken ? "Replace token (leave blank to keep)" : "ghp_… or github_pat_…"
            }
            className="h-8 text-[12px] font-mono"
          />
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="size-8 p-0"
            onClick={() => setShow((v) => !v)}
            title={show ? "Hide" : "Show"}
          >
            {show ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
          </Button>
        </div>

        <Input
          value={defaultOwner}
          onChange={(e) => setDefaultOwner(e.target.value)}
          placeholder="Default owner (e.g. your GitHub username) — optional"
          className="h-8 text-[12px]"
        />

        <p className="text-[11px] text-muted-foreground leading-relaxed">
          Needs <code className="font-mono">repo</code> scope (classic) or
          contents:write + workflows on a fine-grained PAT. The repo must
          already exist on GitHub before pushing.
        </p>

        <div className="flex items-center gap-2 pt-1">
          <Button size="sm" onClick={save} disabled={update.isPending}>
            Save
          </Button>
          {hasToken && (
            <Button
              size="sm"
              variant="ghost"
              onClick={clear}
              disabled={update.isPending}
              className="text-muted-foreground"
            >
              <Trash2 className="mr-1 size-3" />
              Remove token
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
