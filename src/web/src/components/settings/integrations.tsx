"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Check, Eye, EyeOff, GitBranch, Trash2, Bot, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SettingsSection } from "./field";
import { useSettings, useUpdateSettings } from "@/hooks/use-settings";

// GitHub App slug — install page is github.com/apps/<slug>. Matches the
// gh-app deploy (`GH_APP_BOT_LOGIN=jarvis-gh-bot[bot]`, real slug jarvis-gh-bot).
const GH_BOT_SLUG = "jarvis-gh-bot";

export function IntegrationsSection() {
  const { data } = useSettings();
  if (!data) return <p className="text-sm text-muted-foreground">Loading…</p>;

  return (
    <SettingsSection
      description="Connect Jarvis to GitHub — install the bot on your repos, and store a token for pushing Workbench workspaces. Tokens stay local in ~/.jarvis/settings.json."
    >
      <JarvisBotRow />
      <div className="my-5 border-t border-border/60" />
      <GitHubRow />
    </SettingsSection>
  );
}

// The Jarvis GitHub App (@jarvis-gh-bot): install it on a repo, then mention
// @jarvis-gh-bot in an issue or PR comment to dispatch a watchable /code
// session that opens a PR. This is the discoverable "where is the bot" home.
function JarvisBotRow() {
  const installUrl = `https://github.com/apps/${GH_BOT_SLUG}/installations/new`;
  const appUrl = `https://github.com/apps/${GH_BOT_SLUG}`;
  return (
    <div>
      <div className="mb-1.5 flex items-center gap-2">
        <Bot className="size-4 text-muted-foreground" />
        <h3 className="text-[15px] font-semibold">Jarvis GitHub Bot</h3>
        <code className="rounded bg-accent/50 px-1.5 py-0.5 text-[11px] text-muted-foreground">
          @{GH_BOT_SLUG}
        </code>
      </div>
      <p className="mb-3 text-[13px] text-muted-foreground">
        Install the bot on your repositories, then mention{" "}
        <code className="text-[12px]">@{GH_BOT_SLUG} &lt;task&gt;</code> in any issue or pull-request
        comment. It runs a watchable{" "}
        <span className="font-medium text-foreground">Code</span> session and opens a pull request
        with a link back to the live session — the same flow as{" "}
        <code className="text-[12px]">jarvis cloud</code>, driven from GitHub.
      </p>
      <div className="flex flex-wrap gap-2">
        <a href={installUrl} target="_blank" rel="noopener noreferrer">
          <Button size="sm">
            <Bot className="mr-1.5 size-3.5" /> Install on GitHub
          </Button>
        </a>
        <a href={appUrl} target="_blank" rel="noopener noreferrer">
          <Button variant="outline" size="sm">
            <ExternalLink className="mr-1.5 size-3.5" /> View app page
          </Button>
        </a>
      </div>
    </div>
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
