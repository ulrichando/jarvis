"use client";

import { Sparkles, Plus } from "lucide-react";

/**
 * Personal-scoped skills — reusable slash commands JARVIS can invoke
 * across any chat. Workspace-scoped skills live in the workbench
 * Settings tab → Skills.
 *
 * Stub for now. The Anthropic Skills format spec (YAML frontmatter +
 * markdown body) is the natural starting point — see references at
 * the bottom of the section.
 */
export function SkillsSection() {
  return (
    <div className="space-y-6">
      <div>
        <div className="flex items-center gap-2">
          <Sparkles className="size-4 text-muted-foreground" />
          <h2 className="text-lg font-semibold">Skills</h2>
          <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[9px] uppercase tracking-wider text-amber-500">
            Coming soon
          </span>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          Reusable commands JARVIS can invoke — slash commands, prompt
          templates, or shell macros. Bring your own or install from the
          community registry.
        </p>
      </div>

      <div className="flex items-center justify-between">
        <h3 className="text-[13px] font-medium">Your skills</h3>
        <button
          type="button"
          disabled
          className="inline-flex cursor-not-allowed items-center gap-1.5 rounded-md border border-border/60 bg-card/40 px-3 py-1.5 text-[12px] text-muted-foreground/70"
        >
          <Plus className="size-3.5" />
          New skill
        </button>
      </div>

      <div className="rounded-lg border border-border/50 bg-card/20 p-6 text-center text-[13px] text-muted-foreground">
        No skills yet.
      </div>

      <div className="rounded-lg border border-border/50 bg-card/30 p-4">
        <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          What this section will do
        </h3>
        <ul className="space-y-1.5 text-[13px] text-foreground/85">
          <li className="flex items-start gap-2">
            <span className="mt-1.5 size-1 shrink-0 rounded-full bg-primary/60" />
            <span>
              Author skills as YAML frontmatter + markdown body (Anthropic&apos;s skills
              format).
            </span>
          </li>
          <li className="flex items-start gap-2">
            <span className="mt-1.5 size-1 shrink-0 rounded-full bg-primary/60" />
            <span>
              Bind shell commands as skills (e.g. <code className="font-mono">/lint</code> runs{" "}
              <code className="font-mono">bunx eslint</code> in the active workspace).
            </span>
          </li>
          <li className="flex items-start gap-2">
            <span className="mt-1.5 size-1 shrink-0 rounded-full bg-primary/60" />
            <span>Slash-command auto-complete in the composer.</span>
          </li>
          <li className="flex items-start gap-2">
            <span className="mt-1.5 size-1 shrink-0 rounded-full bg-primary/60" />
            <span>Browse + install from a community registry; pin versions.</span>
          </li>
        </ul>
      </div>

      <div className="rounded-lg border border-border/40 bg-card/20 p-4">
        <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          What&apos;s needed to wire it up
        </h3>
        <ul className="space-y-1 text-[12px] text-muted-foreground">
          <li className="font-mono">· Skill format: YAML frontmatter (name, description, args) + markdown body</li>
          <li className="font-mono">· Storage: ~/.jarvis/skills/&lt;name&gt;.md (per-user)</li>
          <li className="font-mono">· Skill resolver in composer: parse `/&lt;name&gt; &lt;args&gt;`</li>
          <li className="font-mono">· Optional: registry index (jarvis.dev/skills) + signed install</li>
        </ul>
      </div>

      <div>
        <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          Preview — what populated state looks like
        </h3>
        <div className="space-y-1.5 opacity-60">
          {[
            {
              name: "/optimize-images",
              kind: "shell",
              desc: "Compress all PNG/JPG in public/ via sharp",
            },
            {
              name: "/release-notes",
              kind: "prompt",
              desc: "Summarize git log since last tag into release notes",
            },
            {
              name: "/scaffold-test",
              kind: "prompt",
              desc: "Generate a Vitest spec for the current open file",
            },
          ].map((s) => (
            <div
              key={s.name}
              className="flex items-center gap-3 rounded-md border border-border/40 px-3 py-2 text-[12px]"
            >
              <span className="font-mono text-foreground">{s.name}</span>
              <span className="rounded bg-muted px-1.5 py-0 text-[10px] uppercase tracking-wider text-muted-foreground">
                {s.kind}
              </span>
              <span className="flex-1 truncate text-muted-foreground">
                {s.desc}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
