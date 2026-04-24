import { SettingsSection } from "./field";

const SHORTCUTS: Array<{ keys: string; description: string; section: string }> = [
  { section: "Chat", keys: "⏎", description: "Send message" },
  { section: "Chat", keys: "⇧⏎", description: "New line in composer" },
  { section: "Chat", keys: "⌘K", description: "Start a new chat" },
  { section: "Chat", keys: "⌘/", description: "Search chats" },
  { section: "Chat", keys: "Esc", description: "Stop streaming response" },
  { section: "App", keys: "⌘B", description: "Toggle sidebar" },
  { section: "App", keys: "⌘E", description: "Toggle preview panel" },
  { section: "App", keys: "⌘,", description: "Open settings" },
];

export function KeyboardSection() {
  const bySection = SHORTCUTS.reduce<Record<string, typeof SHORTCUTS>>(
    (acc, s) => {
      (acc[s.section] ??= []).push(s);
      return acc;
    },
    {},
  );

  return (
    <SettingsSection description="Shortcuts are view-only for now. Remapping is coming.">
      {Object.entries(bySection).map(([section, rows]) => (
        <div key={section}>
          <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
            {section}
          </div>
          <div className="divide-y divide-border/40 rounded-md border border-border/60 bg-background/40">
            {rows.map((s) => (
              <div
                key={s.keys + s.description}
                className="flex items-center justify-between px-3 py-2"
              >
                <span className="text-sm text-foreground/90">
                  {s.description}
                </span>
                <kbd className="rounded-md border border-border/80 bg-muted/40 px-2 py-0.5 font-mono text-[11px] text-foreground">
                  {s.keys}
                </kbd>
              </div>
            ))}
          </div>
        </div>
      ))}
    </SettingsSection>
  );
}
