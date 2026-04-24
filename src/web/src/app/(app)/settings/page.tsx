"use client";

import { useState } from "react";
import {
  Settings as SettingsIcon,
  User,
  KeyRound,
  Palette,
  Keyboard,
  Database,
  Info,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { GeneralSection } from "@/components/settings/general";
import { ProvidersSection } from "@/components/settings/providers";
import { AppearanceSection } from "@/components/settings/appearance";
import { KeyboardSection } from "@/components/settings/keyboard";
import { DataSection } from "@/components/settings/data";
import { AboutSection } from "@/components/settings/about";

type Section =
  | "general"
  | "providers"
  | "appearance"
  | "keyboard"
  | "data"
  | "about";

const NAV: Array<{
  id: Section;
  label: string;
  icon: typeof SettingsIcon;
}> = [
  { id: "general", label: "General", icon: User },
  { id: "providers", label: "Providers", icon: KeyRound },
  { id: "appearance", label: "Appearance", icon: Palette },
  { id: "keyboard", label: "Keyboard", icon: Keyboard },
  { id: "data", label: "Data", icon: Database },
  { id: "about", label: "About", icon: Info },
];

export default function SettingsPage() {
  const [section, setSection] = useState<Section>("general");
  const active = NAV.find((n) => n.id === section)!;

  return (
    <div className="flex h-full">
      {/* Settings nav */}
      <aside className="flex w-56 shrink-0 flex-col border-r border-border/60 bg-sidebar/30 p-3">
        <div className="flex items-center gap-2 px-2 py-2 font-mono text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
          <SettingsIcon className="size-3.5 text-primary" />
          settings
        </div>
        <nav className="mt-2 space-y-px">
          {NAV.map((item) => {
            const isActive = item.id === section;
            return (
              <button
                key={item.id}
                onClick={() => setSection(item.id)}
                className={cn(
                  "group flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-[13px] transition-colors",
                  isActive
                    ? "bg-sidebar-accent text-sidebar-accent-foreground"
                    : "text-sidebar-foreground/80 hover:bg-sidebar-accent/60",
                )}
              >
                <item.icon
                  className={cn(
                    "size-3.5 shrink-0",
                    isActive
                      ? "text-primary"
                      : "text-sidebar-foreground/50 group-hover:text-primary",
                  )}
                />
                {item.label}
              </button>
            );
          })}
        </nav>
      </aside>

      {/* Section content */}
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-2xl px-8 py-10">
          <header className="mb-8 flex items-center gap-3">
            <active.icon className="size-5 text-primary" />
            <h1 className="text-xl font-semibold tracking-tight">
              {active.label}
            </h1>
          </header>

          {section === "general" && <GeneralSection />}
          {section === "providers" && <ProvidersSection />}
          {section === "appearance" && <AppearanceSection />}
          {section === "keyboard" && <KeyboardSection />}
          {section === "data" && <DataSection />}
          {section === "about" && <AboutSection />}
        </div>
      </div>
    </div>
  );
}
