"use client";

import { useEffect, useState } from "react";
import { Switch } from "@/components/ui/switch";
import { useSettings, useUpdateSettings } from "@/hooks/use-settings";

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-4">
      <h2 className="text-[17px] font-semibold">{children}</h2>
      <div className="mt-2 border-t border-border/60" />
    </div>
  );
}

export function CapabilitiesSection() {
  const { data, isLoading } = useSettings();
  const update = useUpdateSettings();

  const [markdown, setMarkdown] = useState(true);
  const [codeHighlight, setCodeHighlight] = useState(true);
  const [streaming, setStreaming] = useState(true);

  useEffect(() => {
    if (!data) return;
    setMarkdown(data.capabilities?.markdown ?? true);
    setCodeHighlight(data.capabilities?.codeHighlight ?? true);
    setStreaming(data.capabilities?.streaming ?? true);
  }, [data]);

  const toggle = async (
    key: "markdown" | "codeHighlight" | "streaming",
    val: boolean,
  ) => {
    const setters = { markdown: setMarkdown, codeHighlight: setCodeHighlight, streaming: setStreaming };
    setters[key](val);
    try {
      await update.mutateAsync({ capabilities: { [key]: val } });
    } catch {
      setters[key](!val);
    }
  };

  if (isLoading || !data) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }

  return (
    <div className="space-y-10">
      <section>
        <SectionTitle>Response format</SectionTitle>
        <div className="divide-y divide-border/60">
          <div className="flex items-center justify-between py-3.5">
            <div>
              <p className="text-[14px] font-medium">Render markdown</p>
              <p className="mt-0.5 text-[13px] text-muted-foreground">
                Format responses with bold, italic, lists, and headings.
              </p>
            </div>
            <Switch
              checked={markdown}
              onCheckedChange={(v) => toggle("markdown", v)}
            />
          </div>
          <div className="flex items-center justify-between py-3.5">
            <div>
              <p className="text-[14px] font-medium">Code syntax highlighting</p>
              <p className="mt-0.5 text-[13px] text-muted-foreground">
                Colorize code blocks based on language.
              </p>
            </div>
            <Switch
              checked={codeHighlight}
              onCheckedChange={(v) => toggle("codeHighlight", v)}
            />
          </div>
        </div>
      </section>

      <section>
        <SectionTitle>Streaming</SectionTitle>
        <div className="divide-y divide-border/60">
          <div className="flex items-center justify-between py-3.5">
            <div>
              <p className="text-[14px] font-medium">Stream responses in real-time</p>
              <p className="mt-0.5 text-[13px] text-muted-foreground">
                Show tokens as they arrive instead of waiting for the full reply.
              </p>
            </div>
            <Switch
              checked={streaming}
              onCheckedChange={(v) => toggle("streaming", v)}
            />
          </div>
        </div>
      </section>

      <section>
        <SectionTitle>Tools</SectionTitle>
        <div className="divide-y divide-border/60">
          <div className="flex items-center justify-between py-3.5">
            <div>
              <p className="text-[14px] font-medium">Workspace tools</p>
              <p className="mt-0.5 text-[13px] text-muted-foreground">
                Allow Jarvis to read and write files in the active workspace.
              </p>
            </div>
            <span className="rounded-full border border-primary/40 bg-primary/10 px-2.5 py-1 font-mono text-[11px] text-primary">
              enabled
            </span>
          </div>
          <div className="flex items-center justify-between py-3.5">
            <div>
              <p className="text-[14px] font-medium">Shell execution</p>
              <p className="mt-0.5 text-[13px] text-muted-foreground">
                Allow Jarvis to run shell commands inside the workspace sandbox.
              </p>
            </div>
            <span className="rounded-full border border-primary/40 bg-primary/10 px-2.5 py-1 font-mono text-[11px] text-primary">
              enabled
            </span>
          </div>
        </div>
      </section>
    </div>
  );
}
