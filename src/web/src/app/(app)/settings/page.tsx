"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { GeneralSection } from "@/components/settings/general";
import { AccountSection } from "@/components/settings/account";
import { PrivacySection } from "@/components/settings/privacy";
import { UsageSection } from "@/components/settings/usage";
import { CapabilitiesSection } from "@/components/settings/capabilities";
import { ConnectorsSection } from "@/components/settings/connectors";
import { IntegrationsSection } from "@/components/settings/integrations";
import { ProvidersSection } from "@/components/settings/providers";
import { DataSection } from "@/components/settings/data";
import { AboutSection } from "@/components/settings/about";
import { JarvisInChromeSection } from "@/components/settings/jarvis-in-chrome";
// VoiceAndModelsSection removed 2026-05-22 along with the rest of the
// hub subsystem — voice/cli model + TTS provider are now configured
// via the desktop tray's settings panel, which writes the flat files
// that pipeline.settings.read_unified_setting reads.
import { KnowledgeSection } from "@/components/settings/knowledge";
import { SkillsSection } from "@/components/settings/skills";
import { CookbookSection } from "@/components/settings/cookbook";
import { SecuritySection } from "@/components/settings/security";

type Section =
  | "general"
  | "account"
  | "security"
  | "applications"
  | "knowledge"
  | "skills"
  | "privacy"
  | "usage"
  | "capabilities"
  | "connectors"
  | "providers"
  | "cookbook"
  | "data"
  | "about"
  | "jarvis-in-chrome";

const NAV: Array<{ id: Section; label: string }> = [
  { id: "general", label: "General" },
  { id: "account", label: "Account" },
  { id: "security", label: "Security" },
  { id: "applications", label: "Applications" },
  { id: "knowledge", label: "Knowledge" },
  { id: "skills", label: "Skills" },
  { id: "connectors", label: "Connectors (MCP)" },
  { id: "providers", label: "Providers" },
  { id: "cookbook", label: "Cookbook" },
  { id: "capabilities", label: "Capabilities" },
  { id: "usage", label: "Usage" },
  { id: "privacy", label: "Privacy" },
  { id: "data", label: "Data" },
  { id: "about", label: "About" },
  { id: "jarvis-in-chrome", label: "Jarvis in Chrome" },
];

export default function SettingsPage() {
  const [section, setSection] = useState<Section>("general");

  // Honor ?tab=<section> deep links (e.g. /settings?tab=usage from the /code
  // usage popover). Post-mount to avoid an SSR/client hydration mismatch.
  useEffect(() => {
    const tab = new URLSearchParams(window.location.search).get("tab");
    if (tab && NAV.some((n) => n.id === tab)) setSection(tab as Section);
  }, []);

  return (
    <div className="flex h-full">
      <aside className="flex w-52 shrink-0 flex-col border-r border-border/60 bg-sidebar/30 px-3 py-5">
        <h1 className="px-2 pb-3 text-[20px] font-semibold tracking-tight">
          Settings
        </h1>
        <nav className="space-y-0.5">
          {NAV.map((item) => {
            const isActive = item.id === section;
            return (
              <button
                key={item.id}
                onClick={() => setSection(item.id)}
                className={cn(
                  "w-full rounded-md px-2 py-1.5 text-left text-[14px] transition-colors",
                  isActive
                    ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
                    : "text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground",
                )}
              >
                {item.label}
              </button>
            );
          })}
        </nav>
      </aside>

      <div className="flex-1 overflow-hidden">
        {section === "cookbook" ? (
          // Full-bleed: the Cookbook is an embedded app, not a settings form,
          // so it breaks out of the max-w-2xl reading column.
          <CookbookSection />
        ) : (
          <div className="h-full overflow-y-auto">
            <div className="mx-auto max-w-2xl px-8 py-8">
              {section === "general" && <GeneralSection />}
              {section === "account" && <AccountSection />}
              {section === "security" && <SecuritySection />}
              {section === "applications" && <IntegrationsSection />}
              {section === "knowledge" && <KnowledgeSection />}
              {section === "skills" && <SkillsSection />}
              {section === "privacy" && <PrivacySection />}
              {section === "usage" && <UsageSection />}
              {section === "capabilities" && <CapabilitiesSection />}
              {section === "connectors" && <ConnectorsSection />}
              {section === "providers" && <ProvidersSection />}
              {section === "data" && <DataSection />}
              {section === "about" && <AboutSection />}
              {section === "jarvis-in-chrome" && <JarvisInChromeSection />}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
