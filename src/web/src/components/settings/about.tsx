import { Cpu } from "lucide-react";
import { SettingsSection } from "./field";

const INFO = [
  { label: "Version", value: "0.0.1 · dev" },
  { label: "Framework", value: "Next.js 16 · Turbopack" },
  { label: "Runtime", value: "Bun + Node" },
  { label: "Providers", value: "Anthropic · OpenAI · Google · DeepSeek · Kimi" },
];

export function AboutSection() {
  return (
    <>
      <SettingsSection>
        <div className="flex items-center gap-3">
          <div className="flex size-11 items-center justify-center rounded-lg border border-primary/40 bg-primary/10">
            <Cpu className="size-5 text-primary" />
          </div>
          <div>
            <h3 className="text-base font-semibold">Jarvis</h3>
            <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
              personal ai workbench
            </p>
          </div>
        </div>
      </SettingsSection>

      <SettingsSection title="Build">
        <dl className="divide-y divide-border/40">
          {INFO.map((row) => (
            <div key={row.label} className="flex items-center justify-between py-1.5">
              <dt className="text-sm text-muted-foreground">{row.label}</dt>
              <dd className="font-mono text-xs text-foreground/90">{row.value}</dd>
            </div>
          ))}
        </dl>
      </SettingsSection>
    </>
  );
}
