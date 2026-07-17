"use client";

import { useEffect, useMemo, useState } from "react";
import {
  BarChart3,
  BookMarked,
  CircleUser,
  Database,
  GraduationCap,
  Info,
  KeyRound,
  LayoutGrid,
  Lock,
  Plug,
  Puzzle,
  Search,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Terminal,
  X,
  type LucideIcon,
} from "lucide-react";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { useUI } from "@/stores/ui";
import { cn } from "@/lib/utils";
import { GeneralSection } from "./general";
import { AccountSection } from "./account";
import { PrivacySection } from "./privacy";
import { UsageSection } from "./usage";
import { CapabilitiesSection } from "./capabilities";
import { ConnectorsSection } from "./connectors";
import { ApiTokensSection } from "./api-tokens";
import { IntegrationsSection } from "./integrations";
import { ProvidersSection } from "./providers";
import { DataSection } from "./data";
import { AboutSection } from "./about";
import { JarvisInChromeSection } from "./jarvis-in-chrome";
import { KnowledgeSection } from "./knowledge";
import { SkillsSection } from "./skills";
import { SecuritySection } from "./security";

type Group = "Settings" | "Customize";
type Item = { id: string; label: string; icon: LucideIcon; group: Group };

// claude.ai splits the settings modal into two groups: account-level
// Settings and the Customize group (Skills / Connectors / …). We map the
// same two-group shape onto JARVIS's real sections — no fabricated billing.
const NAV: Item[] = [
  { id: "general", label: "General", icon: Settings2, group: "Settings" },
  { id: "account", label: "Account", icon: CircleUser, group: "Settings" },
  { id: "security", label: "Security", icon: ShieldCheck, group: "Settings" },
  { id: "privacy", label: "Privacy", icon: Lock, group: "Settings" },
  { id: "providers", label: "Providers", icon: KeyRound, group: "Settings" },
  { id: "usage", label: "Usage", icon: BarChart3, group: "Settings" },
  { id: "capabilities", label: "Capabilities", icon: SlidersHorizontal, group: "Settings" },
  { id: "api-tokens", label: "API Tokens", icon: Terminal, group: "Settings" },
  { id: "data", label: "Data", icon: Database, group: "Settings" },
  { id: "about", label: "About", icon: Info, group: "Settings" },
  { id: "jarvis-in-chrome", label: "Jarvis in Chrome", icon: Puzzle, group: "Settings" },
  { id: "skills", label: "Skills", icon: GraduationCap, group: "Customize" },
  { id: "connectors", label: "Connectors", icon: Plug, group: "Customize" },
  { id: "knowledge", label: "Knowledge", icon: BookMarked, group: "Customize" },
  { id: "applications", label: "Applications", icon: LayoutGrid, group: "Customize" },
];

const GROUPS: Group[] = ["Settings", "Customize"];

function SectionBody({
  section,
  onNavigate,
}: {
  section: string;
  onNavigate: (id: string) => void;
}) {
  switch (section) {
    case "general":
      return <GeneralSection />;
    case "account":
      return <AccountSection />;
    case "security":
      return <SecuritySection />;
    case "privacy":
      return <PrivacySection />;
    case "providers":
      return <ProvidersSection />;
    case "usage":
      return <UsageSection onOpenSection={onNavigate} />;
    case "capabilities":
      return <CapabilitiesSection />;
    case "api-tokens":
      return <ApiTokensSection />;
    case "data":
      return <DataSection />;
    case "about":
      return <AboutSection />;
    case "jarvis-in-chrome":
      return <JarvisInChromeSection />;
    case "skills":
      return <SkillsSection />;
    case "connectors":
      return <ConnectorsSection />;
    case "knowledge":
      return <KnowledgeSection />;
    case "applications":
      return <IntegrationsSection />;
    default:
      return <GeneralSection />;
  }
}

// The Settings/Customize modal — mounted once in the app shell, opened via
// useUI().openSettings(section). Overlays the current view (claude.ai
// behavior) instead of navigating to a page.
export function SettingsDialog() {
  const { settingsOpen, settingsSection, openSettings, closeSettings } = useUI();
  const [section, setSection] = useState("general");
  const [q, setQ] = useState("");

  // Land on the requested section each time the dialog opens.
  useEffect(() => {
    if (settingsOpen && settingsSection) setSection(settingsSection);
  }, [settingsOpen, settingsSection]);

  const filtered = useMemo(() => {
    const query = q.trim().toLowerCase();
    if (!query) return NAV;
    return NAV.filter((n) => n.label.toLowerCase().includes(query));
  }, [q]);

  const active = NAV.find((n) => n.id === section) ?? NAV[0];

  return (
    <Dialog open={settingsOpen} onOpenChange={(o) => !o && closeSettings()}>
      <DialogContent
        showCloseButton={false}
        className="grid h-[82vh] max-h-[820px] w-[calc(100%-2rem)] max-w-4xl grid-cols-1 gap-0 overflow-hidden p-0 sm:max-w-4xl md:grid-cols-[248px_1fr]"
      >
        {/* Left rail: search + grouped nav */}
        <aside className="hidden flex-col border-r border-border/60 bg-sidebar/40 p-3 md:flex">
          <div className="relative mb-3">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground/60" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search"
              // The provider API-key fields are type="password", which makes
              // Chrome treat this dialog as a login form and autofill the saved
              // email into the first text field (this search box). Opt out.
              autoComplete="off"
              name="settings-search"
              className="w-full rounded-lg border border-border/50 bg-card/50 py-1.5 pl-8 pr-3 text-[13px] outline-none transition-colors placeholder:text-muted-foreground/60 focus:border-border focus:bg-card"
            />
          </div>
          <nav className="flex-1 space-y-4 overflow-y-auto">
            {GROUPS.map((group) => {
              const items = filtered.filter((n) => n.group === group);
              if (items.length === 0) return null;
              return (
                <div key={group}>
                  <div className="px-2 pb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground/60">
                    {group}
                  </div>
                  <div className="space-y-0.5">
                    {items.map((item) => (
                      <button
                        key={item.id}
                        onClick={() => setSection(item.id)}
                        className={cn(
                          "flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-[13.5px] transition-colors",
                          item.id === section
                            ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
                            : "text-sidebar-foreground/75 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground",
                        )}
                      >
                        <item.icon className="size-4 shrink-0 text-muted-foreground" />
                        {item.label}
                      </button>
                    ))}
                  </div>
                </div>
              );
            })}
          </nav>
        </aside>

        {/* Right pane: header + section body */}
        <div className="flex min-h-0 flex-col">
          <div className="flex shrink-0 items-center justify-between border-b border-border/50 px-5 py-3">
            <div className="flex items-center gap-2 text-[15px] font-semibold">
              <active.icon className="size-4 text-muted-foreground" />
              {active.label}
            </div>
            <button
              type="button"
              onClick={closeSettings}
              aria-label="Close settings"
              className="flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent/50 hover:text-foreground"
            >
              <X className="size-4" />
            </button>
          </div>
          {/* Mobile group tabs (no left rail on small screens) */}
          <nav className="flex gap-1 overflow-x-auto border-b border-border/50 px-3 py-2 md:hidden [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            {NAV.map((item) => (
              <button
                key={item.id}
                onClick={() => setSection(item.id)}
                className={cn(
                  "shrink-0 whitespace-nowrap rounded-full border px-3 py-1 text-[12.5px] transition-colors",
                  item.id === section
                    ? "border-primary/50 bg-primary/10 font-medium text-primary"
                    : "border-border/60 text-foreground/70",
                )}
              >
                {item.label}
              </button>
            ))}
          </nav>
          <div className="min-h-0 flex-1 overflow-y-auto">
            <div className="mx-auto max-w-2xl px-6 py-6">
              <SectionBody section={section} onNavigate={(id) => {
                if (NAV.some((n) => n.id === id)) setSection(id);
                else openSettings(id);
              }} />
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
