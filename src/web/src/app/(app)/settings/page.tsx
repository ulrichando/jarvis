"use client";

import { useState } from "react";
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
import { VoiceAndModelsSection } from "@/components/settings/voice-and-models";

type Section =
  | "general"
  | "account"
  | "privacy"
  | "usage"
  | "capabilities"
  | "connectors"
  | "integrations"
  | "providers"
  | "voice-and-models"
  | "data"
  | "about"
  | "jarvis-in-chrome";

const NAV: Array<{ id: Section; label: string }> = [
  { id: "general", label: "General" },
  { id: "account", label: "Account" },
  { id: "privacy", label: "Privacy" },
  { id: "usage", label: "Usage" },
  { id: "capabilities", label: "Capabilities" },
  { id: "connectors", label: "Connectors" },
  { id: "integrations", label: "Integrations" },
  { id: "providers", label: "Providers" },
  { id: "voice-and-models", label: "Voice & Models" },
  { id: "data", label: "Data" },
  { id: "about", label: "About" },
  { id: "jarvis-in-chrome", label: "Jarvis in Chrome" },
];

export default function SettingsPage() {
  const [section, setSection] = useState<Section>("general");

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

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-2xl px-8 py-8">
          {section === "general" && <GeneralSection />}
          {section === "account" && <AccountSection />}
          {section === "privacy" && <PrivacySection />}
          {section === "usage" && <UsageSection />}
          {section === "capabilities" && <CapabilitiesSection />}
          {section === "connectors" && <ConnectorsSection />}
          {section === "integrations" && <IntegrationsSection />}
          {section === "providers" && <ProvidersSection />}
          {section === "voice-and-models" && <VoiceAndModelsSection />}
          {section === "data" && <DataSection />}
          {section === "about" && <AboutSection />}
          {section === "jarvis-in-chrome" && <JarvisInChromeSection />}
        </div>
      </div>
    </div>
  );
}
