"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { SettingsSection, Field } from "./field";
import { useSettings, useUpdateSettings } from "@/hooks/use-settings";
import { modelsByProvider } from "@/lib/ai/models-meta";
import { Fragment } from "react";

export function GeneralSection() {
  const { data, isLoading } = useSettings();
  const update = useUpdateSettings();
  const groups = modelsByProvider();

  const [name, setName] = useState("");
  const [model, setModel] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [temperature, setTemperature] = useState(0.7);

  useEffect(() => {
    if (!data) return;
    setName(data.user.name ?? "");
    setModel(data.defaults.model);
    setSystemPrompt(data.defaults.systemPrompt ?? "");
    setTemperature(data.defaults.temperature);
  }, [data]);

  const save = async () => {
    try {
      await update.mutateAsync({
        user: { name: name.trim() || undefined },
        defaults: {
          model,
          systemPrompt: systemPrompt.trim() || undefined,
          temperature,
        },
      });
      toast.success("Settings saved");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save");
    }
  };

  if (isLoading || !data) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }

  return (
    <>
      <SettingsSection title="You" description="How Jarvis refers to you.">
        <Field label="Name">
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Your name"
            maxLength={80}
          />
        </Field>
      </SettingsSection>

      <SettingsSection
        title="Defaults"
        description="Applied to every new conversation unless overridden."
      >
        <Field label="Default model">
          <Select value={model} onValueChange={(v) => v && setModel(v)}>
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {groups.map((g, gi) => (
                <Fragment key={g.provider}>
                  {gi > 0 && <SelectSeparator />}
                  <SelectGroup>
                    <SelectLabel className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                      {g.label}
                    </SelectLabel>
                    {g.models.map((m) => (
                      <SelectItem key={m.id} value={m.id}>
                        {m.label}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </Fragment>
              ))}
            </SelectContent>
          </Select>
        </Field>

        <Field
          label="Custom system prompt"
          hint="Prepended to every conversation. Leave blank for the default personality."
        >
          <Textarea
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            placeholder="e.g. Always respond in concise bullet points. Assume I'm an experienced TypeScript developer."
            rows={6}
            className="font-mono text-sm"
          />
        </Field>

        <Field
          label={`Temperature · ${temperature.toFixed(2)}`}
          hint="0 = deterministic. 1 = balanced. 2 = chaotic."
        >
          <input
            type="range"
            min={0}
            max={2}
            step={0.05}
            value={temperature}
            onChange={(e) => setTemperature(Number(e.target.value))}
            className="w-full accent-primary"
          />
        </Field>
      </SettingsSection>

      <div className="flex justify-end">
        <Button onClick={save} disabled={update.isPending}>
          {update.isPending ? "Saving…" : "Save changes"}
        </Button>
      </div>
    </>
  );
}
