"use client";

import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Globe } from "lucide-react";

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-4">
      <h2 className="text-[17px] font-semibold">{children}</h2>
      <div className="mt-2 border-t border-border/60" />
    </div>
  );
}

export function JarvisInChromeSection() {
  return (
    <div className="space-y-10">
      <section>
        <div className="mb-6 flex items-start gap-3 rounded-xl border border-border/60 bg-card/40 p-4">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10">
            <Globe className="size-4.5 text-primary" />
          </div>
          <div>
            <p className="text-[14px] font-semibold">Jarvis in Chrome</p>
            <p className="mt-0.5 text-[13px] text-muted-foreground">
              The browser extension lets you use Jarvis on any web page. Install
              it from the Chrome Web Store once it&apos;s available.
            </p>
          </div>
        </div>
      </section>

      <section>
        <SectionTitle>Jarvis in Chrome settings</SectionTitle>
        <div className="divide-y divide-border/60">
          <div className="flex items-center justify-between py-3.5">
            <div>
              <p className="text-[14px] font-medium">Default for all sites</p>
              <p className="mt-0.5 text-[13px] text-muted-foreground">
                Choose whether Jarvis in Chrome works on all sites by default.
              </p>
            </div>
            <Select
              defaultValue="allow"
              onValueChange={() =>
                toast.message("Jarvis in Chrome — not yet available", {
                  description: "The browser extension is coming in a future release.",
                })
              }
            >
              <SelectTrigger className="w-44">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="allow">Allow extension</SelectItem>
                <SelectItem value="block">Block extension</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        <div className="mt-3 rounded-lg border border-border/50 bg-card/30 px-4 py-3">
          <p className="text-[13px] text-muted-foreground">
            Jarvis in Chrome works everywhere except sites you block below.
          </p>
        </div>
      </section>

      <section>
        <SectionTitle>Blocked sites</SectionTitle>
        <div className="divide-y divide-border/60">
          <div className="flex items-center justify-between py-3.5">
            <p className="text-[13px] text-muted-foreground">
              Jarvis in Chrome cannot be used on these sites.
            </p>
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                toast.message("Blocked sites — coming soon", {
                  description: "Manage per-site extension access once the extension is available.",
                })
              }
            >
              Add websites
            </Button>
          </div>
        </div>
      </section>
    </div>
  );
}
