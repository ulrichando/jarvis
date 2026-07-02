"use client";

import { useState } from "react";
import { Brush } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type Single = { id: string; question: string; hint?: string; options: string[]; allowOther?: boolean };
type Multi = { id: string; question: string; hint?: string; options: string[]; allowOther?: boolean };

const ARTIFACT: Single = {
  id: "artifact",
  question: "What are we making?",
  hint: "This shapes everything else.",
  options: [
    "Slide deck",
    "Interactive prototype",
    "Landing page",
    "One-pager",
    "Infographic",
    "Explore a few options",
    "Decide for me",
  ],
  allowOther: true,
};

const AUDIENCE: Single = {
  id: "audience",
  question: "Who is it for?",
  options: [
    "Pitching investors",
    "Showing teammates internally",
    "A personal portfolio piece",
    "Marketing to end users",
    "Just exploring an idea",
    "Decide for me",
  ],
  allowOther: true,
};

const AESTHETIC: Multi = {
  id: "aesthetic",
  question: "Aesthetic direction",
  hint: "Pick one or a few.",
  options: [
    "Minimal / refined",
    "Editorial / magazine",
    "Retro-futurist / sci-fi",
    "Organic / hand-drawn",
    "Brutalist / raw",
    "Playful / toy-like",
    "Dark / moody",
    "Bright / optimistic",
    "Decide for me",
  ],
  allowOther: true,
};

const SCOPE: Single = {
  id: "scope",
  question: "How much surface?",
  options: [
    "Single hero moment (~1 screen)",
    "Short piece (3–5 screens)",
    "Medium (6–10 screens)",
    "Big (full prototype, longer video)",
    "Decide for me",
  ],
};

export function RefineForm({
  initialTopic = "",
  onContinue,
  onCancel,
}: {
  initialTopic?: string;
  onContinue: (structuredPrompt: string) => void;
  onCancel: () => void;
}) {
  const [topic, setTopic] = useState(initialTopic);
  const [artifact, setArtifact] = useState<string | null>(null);
  const [artifactOther, setArtifactOther] = useState("");
  const [audience, setAudience] = useState<string | null>(null);
  const [audienceOther, setAudienceOther] = useState("");
  const [aesthetic, setAesthetic] = useState<Set<string>>(new Set());
  const [aestheticOther, setAestheticOther] = useState("");
  const [scope, setScope] = useState<string | null>(null);
  const [story, setStory] = useState("");
  const [refs, setRefs] = useState("");

  const valid = topic.trim().length > 0 && artifact != null;

  const submit = () => {
    if (!valid) return;
    const lines: string[] = [];
    lines.push(`Make me a design about: ${topic.trim()}.`);
    const a = artifact === "Other" && artifactOther ? artifactOther.trim() : artifact;
    if (a && a !== "Decide for me") lines.push(`Format: ${a}.`);
    const au = audience === "Other" && audienceOther ? audienceOther.trim() : audience;
    if (au && au !== "Decide for me") lines.push(`Audience: ${au}.`);
    const aes = [...aesthetic].filter((x) => x !== "Decide for me");
    if (aestheticOther.trim()) aes.push(aestheticOther.trim());
    if (aes.length > 0) lines.push(`Aesthetic: ${aes.join(", ")}.`);
    if (scope && scope !== "Decide for me") lines.push(`Scope: ${scope}.`);
    if (story.trim()) lines.push(`Story or message: ${story.trim()}.`);
    if (refs.trim()) lines.push(`References: ${refs.trim()}.`);
    onContinue(lines.join(" "));
  };

  return (
    <div className="flex h-full flex-col bg-background">
      <header className="flex items-center justify-between border-b border-border/60 px-6 py-3">
        <div className="flex items-center gap-2">
          <span className="flex size-6 items-center justify-center rounded-md bg-orange-500/15 text-orange-400">
            <Brush className="size-3.5" />
          </span>
          <div>
            <div className="text-[14px] font-semibold">Refine the brief</div>
            <div className="text-[11px] text-muted-foreground">
              Pick what you want — Jarvis will turn the answers into a prompt you can review before sending.
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={onCancel}>
            Cancel
          </Button>
          <Button size="sm" onClick={submit} disabled={!valid}>
            Continue
          </Button>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto flex max-w-2xl flex-col gap-7">
          <Field
            label="Topic"
            hint="One line — what's the design about?"
            required
          >
            <input
              autoFocus
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder='e.g. "a coffee subscription called Kindling"'
              className="w-full rounded-md border border-border/60 bg-background px-3 py-2 text-[13px] focus:border-foreground/40 focus:outline-none"
            />
          </Field>

          <SingleField
            spec={ARTIFACT}
            value={artifact}
            other={artifactOther}
            onValueChange={setArtifact}
            onOtherChange={setArtifactOther}
            required
          />

          <SingleField
            spec={AUDIENCE}
            value={audience}
            other={audienceOther}
            onValueChange={setAudience}
            onOtherChange={setAudienceOther}
          />

          <MultiField
            spec={AESTHETIC}
            value={aesthetic}
            other={aestheticOther}
            onToggle={(opt) => {
              const next = new Set(aesthetic);
              if (next.has(opt)) next.delete(opt);
              else next.add(opt);
              setAesthetic(next);
            }}
            onOtherChange={setAestheticOther}
          />

          <SingleField
            spec={SCOPE}
            value={scope}
            onValueChange={setScope}
            other=""
            onOtherChange={() => {}}
          />

          <Field
            label="Story or message (optional)"
            hint='e.g. "capture once, recall forever" or a tagline you want to land.'
          >
            <textarea
              rows={2}
              value={story}
              onChange={(e) => setStory(e.target.value)}
              placeholder="What's the one idea this should leave with the viewer?"
              className="w-full rounded-md border border-border/60 bg-background px-3 py-2 text-[13px] focus:border-foreground/40 focus:outline-none"
            />
          </Field>

          <Field
            label="References (optional)"
            hint="Paste a URL or describe an existing piece you like."
          >
            <textarea
              rows={2}
              value={refs}
              onChange={(e) => setRefs(e.target.value)}
              placeholder="e.g. 'similar feel to Linear's homepage' or a Figma link"
              className="w-full rounded-md border border-border/60 bg-background px-3 py-2 text-[13px] focus:border-foreground/40 focus:outline-none"
            />
          </Field>

          <div className="flex items-center justify-end gap-2 pt-2">
            <Button variant="ghost" size="sm" onClick={onCancel}>
              Cancel
            </Button>
            <Button size="sm" onClick={submit} disabled={!valid}>
              Continue
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

function Field({
  label,
  hint,
  required = false,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2">
      <div>
        <div className="text-[13px] font-semibold text-foreground">
          {label}
          {required && <span className="ml-1 text-orange-400">*</span>}
        </div>
        {hint && <div className="text-[12px] text-muted-foreground">{hint}</div>}
      </div>
      {children}
    </div>
  );
}

function SingleField({
  spec,
  value,
  other,
  onValueChange,
  onOtherChange,
  required = false,
}: {
  spec: Single;
  value: string | null;
  other: string;
  onValueChange: (v: string | null) => void;
  onOtherChange: (v: string) => void;
  required?: boolean;
}) {
  return (
    <Field label={spec.question} hint={spec.hint} required={required}>
      <div className="flex flex-wrap gap-1.5">
        {spec.options.map((o) => (
          <Chip
            key={o}
            label={o}
            active={value === o}
            onClick={() => onValueChange(value === o ? null : o)}
          />
        ))}
        {spec.allowOther && (
          <>
            <Chip
              label="Other"
              active={value === "Other"}
              onClick={() =>
                onValueChange(value === "Other" ? null : "Other")
              }
            />
            {value === "Other" && (
              <input
                value={other}
                onChange={(e) => onOtherChange(e.target.value)}
                placeholder="…"
                className="rounded-full border border-border/60 bg-background px-3 py-1 text-[12px] focus:border-foreground/40 focus:outline-none"
              />
            )}
          </>
        )}
      </div>
    </Field>
  );
}

function MultiField({
  spec,
  value,
  other,
  onToggle,
  onOtherChange,
}: {
  spec: Multi;
  value: Set<string>;
  other: string;
  onToggle: (opt: string) => void;
  onOtherChange: (v: string) => void;
}) {
  return (
    <Field label={spec.question} hint={spec.hint}>
      <div className="flex flex-wrap gap-1.5">
        {spec.options.map((o) => (
          <Chip
            key={o}
            label={o}
            active={value.has(o)}
            onClick={() => onToggle(o)}
          />
        ))}
        {spec.allowOther && (
          <input
            value={other}
            onChange={(e) => onOtherChange(e.target.value)}
            placeholder="Other…"
            className="rounded-full border border-border/60 bg-background px-3 py-1 text-[12px] focus:border-foreground/40 focus:outline-none"
          />
        )}
      </div>
    </Field>
  );
}

function Chip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "rounded-full border px-3 py-1 text-[12px] transition-colors",
        active
          ? "border-foreground bg-foreground text-background"
          : "border-border/60 bg-background text-foreground/85 hover:border-foreground/40",
      )}
    >
      {label}
    </button>
  );
}
