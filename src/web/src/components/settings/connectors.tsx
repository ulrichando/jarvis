"use client";

import { toast } from "sonner";
import { Button } from "@/components/ui/button";

type Connector = {
  id: string;
  name: string;
  description: string;
  status: "connected" | "configure" | "coming_soon";
  icon: string;
};

const CONNECTORS: Connector[] = [
  {
    id: "github",
    name: "GitHub",
    description: "Access repositories and reference code in conversations.",
    status: "coming_soon",
    icon: "GH",
  },
  {
    id: "figma",
    name: "Figma",
    description: "Pull design context and component specs directly into chat.",
    status: "coming_soon",
    icon: "FG",
  },
  {
    id: "vercel",
    name: "Vercel",
    description: "View deployments and logs without leaving Jarvis.",
    status: "coming_soon",
    icon: "VC",
  },
  {
    id: "google-drive",
    name: "Google Drive",
    description: "Reference documents and spreadsheets in your conversations.",
    status: "coming_soon",
    icon: "GD",
  },
  {
    id: "notion",
    name: "Notion",
    description: "Search and reference Notion pages and databases.",
    status: "coming_soon",
    icon: "NO",
  },
];

function ConnectorRow({ connector }: { connector: Connector }) {
  const handleAction = () => {
    toast.message(`${connector.name} — coming soon`, {
      description: "Connector integrations will be available in a future update.",
    });
  };

  return (
    <div className="flex items-center gap-3 py-3.5">
      <div className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-border/60 bg-card/60 font-mono text-[11px] font-bold text-muted-foreground">
        {connector.icon}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-[14px] font-medium">{connector.name}</p>
        <p className="mt-0.5 text-[13px] text-muted-foreground truncate">
          {connector.description}
        </p>
      </div>
      {connector.status === "connected" ? (
        <div className="flex items-center gap-2">
          <span className="text-[13px] text-primary font-medium">Connected</span>
          <Button variant="ghost" size="sm" className="size-7 p-0 text-muted-foreground">
            •••
          </Button>
        </div>
      ) : connector.status === "configure" ? (
        <div className="flex items-center gap-1.5">
          <Button variant="outline" size="sm" onClick={handleAction}>
            Configure
          </Button>
          <Button variant="ghost" size="sm" className="size-7 p-0 text-muted-foreground">
            •••
          </Button>
        </div>
      ) : (
        <Button
          variant="outline"
          size="sm"
          onClick={handleAction}
          className="text-muted-foreground"
          disabled
        >
          Coming soon
        </Button>
      )}
    </div>
  );
}

export function ConnectorsSection() {
  return (
    <div className="space-y-10">
      <section>
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="text-[17px] font-semibold">Connectors</h2>
            <p className="mt-0.5 text-[13px] text-muted-foreground">
              Allow Jarvis to reference other apps and services.
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => toast.message("Connector marketplace — coming soon")}
          >
            Browse connectors
          </Button>
        </div>
        <div className="border-t border-border/60 divide-y divide-border/60">
          {CONNECTORS.map((c) => (
            <ConnectorRow key={c.id} connector={c} />
          ))}
        </div>
      </section>

      <section>
        <div className="border-t border-border/60 pt-4">
          <Button
            variant="outline"
            size="sm"
            onClick={() => toast.message("Custom connectors — coming soon")}
          >
            Add custom connector
          </Button>
        </div>
      </section>
    </div>
  );
}
