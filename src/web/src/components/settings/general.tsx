"use client";

import { useEffect, useRef, useState, Fragment } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2, Volume2 } from "lucide-react";
import { toast } from "sonner";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
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
import { useSettings, useUpdateSettings } from "@/hooks/use-settings";
import { useChatStore } from "@/stores/chat";
import { modelsByProvider, MODELS_META, type ModelId } from "@/lib/ai/models-meta";
import { IMAGE_MODELS } from "@/lib/ai/image-models";
import { kokoroVoiceAccent, kokoroVoiceLabel } from "@/lib/chat/voices";
import { cn } from "@/lib/utils";

const JOB_TITLES = [
  "Engineering",
  "Product",
  "Design",
  "Marketing",
  "Sales",
  "Research",
  "Education",
  "Healthcare",
  "Legal",
  "Finance",
  "Other",
];

function SectionHeader({ title }: { title: string }) {
  return (
    <div className="mb-5">
      <h2 className="text-[17px] font-semibold">{title}</h2>
      <div className="mt-2 border-t border-border/60" />
    </div>
  );
}

export function GeneralSection() {
  const { data, isLoading } = useSettings();
  const update = useUpdateSettings();
  const setChatModel = useChatStore((s) => s.setModel);
  const groups = modelsByProvider();

  const [name, setName] = useState("");
  const [callName, setCallName] = useState("");
  const [jobTitle, setJobTitle] = useState("");
  const [preferences, setPreferences] = useState("");
  const [model, setModel] = useState("");
  const [imageModel, setImageModel] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [temperature, setTemperature] = useState(0.7);
  const [responseCompletions, setResponseCompletions] = useState(false);
  // Kokoro voice id ("af_heart"); empty = server default.
  const [voice, setVoice] = useState("af_heart");
  const [previewing, setPreviewing] = useState<string | null>(null);
  const previewAudioRef = useRef<HTMLAudioElement | null>(null);

  // The REAL voices the local Kokoro engine serves (not invented names).
  const { data: voiceList, isError: voicesDown } = useQuery({
    queryKey: ["tts-voices"],
    queryFn: async () => {
      const r = await fetch("/api/tts/voices");
      if (!r.ok) throw new Error("kokoro unavailable");
      return ((await r.json()) as { voices: string[] }).voices;
    },
    staleTime: 5 * 60_000,
    retry: 1,
  });

  useEffect(() => {
    if (!data) return;
    setName(data.user.name ?? "");
    setCallName(data.user.callName ?? "");
    setJobTitle(data.user.jobTitle ?? "");
    setPreferences(data.user.preferences ?? "");
    setModel(data.defaults.model);
    setImageModel(data.defaults.imageModel);
    setSystemPrompt(data.defaults.systemPrompt ?? "");
    setTemperature(data.defaults.temperature);
    setResponseCompletions(data.notifications?.responseCompletions ?? false);
    setVoice(data.user.voice ?? "af_heart");
  }, [data]);

  const saveProfile = async () => {
    try {
      await update.mutateAsync({
        user: {
          name: name.trim() || undefined,
          callName: callName.trim() || undefined,
          jobTitle: jobTitle || undefined,
          preferences: preferences.trim() || undefined,
        },
        defaults: {
          model,
          imageModel,
          systemPrompt: systemPrompt.trim() || undefined,
          temperature,
        },
      });
      // Apply the new default to the composer's model picker immediately —
      // the picker persists its own choice and would otherwise ignore this.
      if (MODELS_META[model]) setChatModel(model as ModelId);
      toast.success("Saved");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save");
    }
  };

  const toggleNotification = async (val: boolean) => {
    // The chat fires a browser Notification when a reply finishes in a
    // background tab — useless without permission, so ask for it here.
    if (val && typeof Notification !== "undefined") {
      let perm = Notification.permission;
      if (perm === "default") perm = await Notification.requestPermission();
      if (perm !== "granted") {
        toast.error(
          "Notifications are blocked — allow them for this site in the browser, then try again.",
        );
        return;
      }
    }
    setResponseCompletions(val);
    try {
      await update.mutateAsync({ notifications: { responseCompletions: val } });
    } catch {
      setResponseCompletions(!val);
    }
  };

  const applyAppearance = async (patch: {
    fontSize?: "sm" | "md" | "lg";
    density?: "compact" | "cozy";
  }) => {
    try {
      await update.mutateAsync({ appearance: patch });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed");
    }
  };

  // Selecting a voice saves it AND plays a short sample so the choice is
  // audible immediately — the names alone don't tell you anything.
  const applyVoice = async (v: string) => {
    const prev = voice;
    setVoice(v);
    void previewVoice(v);
    try {
      await update.mutateAsync({ user: { voice: v } });
    } catch (err) {
      setVoice(prev);
      toast.error(err instanceof Error ? err.message : "Failed");
    }
  };

  const previewVoice = async (v: string) => {
    setPreviewing(v);
    try {
      const r = await fetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: "Hello — I'm Jarvis, and this is how I sound.",
          voice: v,
        }),
      });
      if (!r.ok) throw new Error("Kokoro TTS isn't reachable");
      const url = URL.createObjectURL(await r.blob());
      previewAudioRef.current?.pause();
      const audio = new Audio(url);
      previewAudioRef.current = audio;
      audio.onended = () => {
        URL.revokeObjectURL(url);
        setPreviewing((p) => (p === v ? null : p));
      };
      await audio.play();
    } catch (err) {
      setPreviewing(null);
      toast.error(err instanceof Error ? err.message : "Preview failed");
    }
  };

  if (isLoading || !data) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }

  return (
    <div className="space-y-10">
      {/* Profile */}
      <section>
        <SectionHeader title="Profile" />

        {/* Two-column row: Full name + Call name (stacked on mobile) */}
        <div className="grid grid-cols-1 gap-4 mb-4 sm:grid-cols-2">
          <div>
            <label className="block text-[14px] font-medium mb-1.5">
              Full name
            </label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Your full name"
              maxLength={80}
            />
          </div>
          <div>
            <label className="block text-[14px] font-medium mb-1.5">
              What should Jarvis call you?{" "}
              <span className="text-destructive">*</span>
            </label>
            <Input
              value={callName}
              onChange={(e) => setCallName(e.target.value)}
              placeholder="Nickname or first name"
              maxLength={40}
            />
          </div>
        </div>

        {/* Job title */}
        <div className="mb-4">
          <label className="block text-[14px] font-medium mb-1.5">
            What best describes your work?
          </label>
          <Select value={jobTitle} onValueChange={(v) => v && setJobTitle(v)}>
            <SelectTrigger className="w-full">
              <SelectValue placeholder="Select a role" />
            </SelectTrigger>
            <SelectContent>
              {JOB_TITLES.map((t) => (
                <SelectItem key={t} value={t}>
                  {t}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {/* Personal preferences */}
        <div className="mb-5">
          <label className="block text-[14px] font-medium mb-0.5">
            What personal preferences should Jarvis consider in responses?
          </label>
          <p className="text-[13px] text-muted-foreground mb-1.5">
            Your preferences will apply to all conversations.
          </p>
          <Textarea
            value={preferences}
            onChange={(e) => setPreferences(e.target.value)}
            placeholder="e.g. Ask clarifying questions before giving detailed answers"
            rows={4}
            maxLength={2000}
          />
        </div>

        <div className="flex justify-end">
          <Button onClick={saveProfile} disabled={update.isPending}>
            {update.isPending ? "Saving…" : "Save changes"}
          </Button>
        </div>
      </section>

      {/* Notifications */}
      <section>
        <SectionHeader title="Notifications" />
        <div className="divide-y divide-border/60">
          <div className="flex items-center justify-between py-3.5">
            <div>
              <p className="text-[14px] font-medium">Response completions</p>
              <p className="mt-0.5 text-[13px] text-muted-foreground">
                Get notified when Jarvis has finished a long response. Most
                useful for complex tasks.
              </p>
            </div>
            <Switch
              checked={responseCompletions}
              onCheckedChange={toggleNotification}
            />
          </div>
        </div>
      </section>

      {/* Appearance */}
      <section>
        <SectionHeader title="Appearance" />

        {/* Font size */}
        <div className="mb-6">
          <p className="text-[14px] font-medium mb-3">Font size</p>
          <div className="flex gap-2">
            {(
              [
                { id: "sm", label: "Small", preview: "text-[11px]" },
                { id: "md", label: "Medium", preview: "text-[14px]" },
                { id: "lg", label: "Large", preview: "text-[17px]" },
              ] as const
            ).map((f) => {
              const active = data.appearance.fontSize === f.id;
              return (
                <button
                  key={f.id}
                  type="button"
                  onClick={() => applyAppearance({ fontSize: f.id })}
                  className={cn(
                    "flex-1 flex flex-col items-center justify-center gap-1.5 rounded-xl border py-4 transition-colors",
                    active
                      ? "border-primary/60 bg-primary/10"
                      : "border-border/60 hover:border-border hover:bg-accent/30",
                  )}
                >
                  <span
                    className={cn(
                      "font-medium text-foreground/80",
                      f.preview,
                    )}
                  >
                    Aa
                  </span>
                  <span className="text-[12px] text-muted-foreground">
                    {f.label}
                  </span>
                </button>
              );
            })}
          </div>
          {/* Live preview — rides the same --chat-fs variable chat replies
              use, so clicking a size visibly changes THIS text instantly. */}
          <div className="mt-3 rounded-lg border border-border/50 bg-card/30 px-4 py-3">
            <p className="text-[length:var(--chat-fs,15px)] leading-[1.7]">
              Preview — chat replies render at this size.
            </p>
          </div>
        </div>

        {/* Density */}
        <div className="mb-2">
          <p className="text-[14px] font-medium mb-3">Density</p>
          <div className="flex gap-2">
            {(
              [
                { id: "compact", label: "Compact" },
                { id: "cozy", label: "Cozy" },
              ] as const
            ).map((d) => {
              const active = data.appearance.density === d.id;
              return (
                <button
                  key={d.id}
                  type="button"
                  onClick={() => applyAppearance({ density: d.id })}
                  className={cn(
                    "flex-1 flex flex-col items-center justify-center gap-2 rounded-xl border py-4 transition-colors",
                    active
                      ? "border-primary/60 bg-primary/10"
                      : "border-border/60 hover:border-border hover:bg-accent/30",
                  )}
                >
                  {/* Spacing preview lines */}
                  <div
                    className={cn(
                      "flex flex-col items-center w-8",
                      d.id === "compact" ? "gap-0.5" : "gap-1.5",
                    )}
                  >
                    {[1, 2, 3].map((i) => (
                      <div
                        key={i}
                        className="h-0.5 w-full rounded-full bg-foreground/30"
                      />
                    ))}
                  </div>
                  <span className="text-[12px] text-muted-foreground">
                    {d.label}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      </section>

      {/* Defaults */}
      <section>
        <SectionHeader title="Defaults" />
        <div className="space-y-4">
          <div>
            <label className="block text-[14px] font-medium mb-1.5">
              Default model
            </label>
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
          </div>

          <div>
            <label className="block text-[14px] font-medium mb-0.5">
              Image model
            </label>
            <p className="text-[13px] text-muted-foreground mb-1.5">
              Used when you ask any chat to generate an image — independent of
              the text model above. Needs an OpenAI or Google API key.
            </p>
            <Select
              value={imageModel}
              onValueChange={(v) => v && setImageModel(v)}
            >
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.values(IMAGE_MODELS).map((m) => {
                  const hasKey = Boolean(
                    (
                      data.providers as Record<
                        string,
                        { hasKey?: boolean } | undefined
                      >
                    )?.[m.provider]?.hasKey,
                  );
                  return (
                    <SelectItem key={m.id} value={m.id} disabled={!hasKey}>
                      {m.label}
                      {hasKey ? "" : " — needs a key"}
                    </SelectItem>
                  );
                })}
              </SelectContent>
            </Select>
          </div>

          <div>
            <label className="block text-[14px] font-medium mb-0.5">
              Custom system prompt
            </label>
            <p className="text-[13px] text-muted-foreground mb-1.5">
              Prepended to every conversation. Leave blank for the default
              personality.
            </p>
            <Textarea
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              placeholder="e.g. Always respond in concise bullet points."
              rows={4}
              className="font-mono text-sm"
            />
          </div>

          <div>
            <label className="block text-[14px] font-medium mb-0.5">
              Temperature · {temperature.toFixed(2)}
            </label>
            <p className="text-[13px] text-muted-foreground mb-2">
              0 = deterministic · 1 = balanced · 2 = creative
            </p>
            <input
              type="range"
              min={0}
              max={2}
              step={0.05}
              value={temperature}
              onChange={(e) => setTemperature(Number(e.target.value))}
              className="w-full accent-primary"
            />
          </div>

          <div className="flex justify-end pt-1">
            <Button onClick={saveProfile} disabled={update.isPending}>
              {update.isPending ? "Saving…" : "Save changes"}
            </Button>
          </div>
        </div>
      </section>

      {/* Voice settings — the live Kokoro voice list, click to hear + save */}
      <section>
        <SectionHeader title="Voice settings" />
        <div>
          <p className="text-[14px] font-medium mb-1">Voice</p>
          <p className="text-[13px] text-muted-foreground mb-3">
            Voices served by your local Kokoro TTS — click one to hear it and
            make it the read-aloud voice for web chat.
          </p>
          {voicesDown ? (
            <p className="rounded-lg border border-border/50 bg-card/30 px-4 py-3 text-[13px] text-muted-foreground">
              Kokoro TTS isn&apos;t reachable (kokoro-tts container on :8880).
              Start it, then reload this page.
            </p>
          ) : !voiceList ? (
            <p className="text-[13px] text-muted-foreground">Loading voices…</p>
          ) : (
            (["af", "am", "bf", "bm"] as const)
              .map((prefix) => ({
                prefix,
                ids: voiceList.filter((v) => v.startsWith(prefix)),
              }))
              .filter((g) => g.ids.length > 0)
              .map((g) => (
                <div key={g.prefix} className="mb-3">
                  <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                    {kokoroVoiceAccent(`${g.prefix}_x`)}
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {g.ids.map((v) => (
                      <button
                        key={v}
                        type="button"
                        onClick={() => applyVoice(v)}
                        className={cn(
                          "flex items-center gap-1.5 rounded-full border px-4 py-1.5 text-[13px] font-medium transition-colors",
                          voice === v
                            ? "border-primary/50 bg-primary/10 text-primary"
                            : "border-border/50 bg-card/30 text-foreground/70 hover:border-border hover:text-foreground",
                        )}
                      >
                        {previewing === v ? (
                          <Loader2 className="size-3 animate-spin" />
                        ) : (
                          voice === v && <Volume2 className="size-3" />
                        )}
                        {kokoroVoiceLabel(v)}
                      </button>
                    ))}
                  </div>
                </div>
              ))
          )}
          <p className="mt-3 text-[12px] text-muted-foreground">
            Used when Jarvis reads replies aloud in the web chat&apos;s voice
            mode. The desktop / voice agent has its own voice settings in the
            tray.
          </p>
        </div>
      </section>
    </div>
  );
}
