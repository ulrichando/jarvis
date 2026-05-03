"use client";

// Voice & Models section — unified-settings surface for the
// hub-managed cross-cutting settings (cli-model, voice-model,
// tts-provider). Reads via GET /api/hub-settings, writes via PUT
// /api/hub-settings, subscribes to /api/events/stream/settings for
// live updates when the tray UI or another subsystem changes a
// value.
//
// keys.env is intentionally NOT exposed here — that's managed by
// the desktop tray's KeysSettings panel, and the hub itself refuses
// to track it. See spec 2026-05-03-jarvis-unified-settings-design.md.

import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type HubSettings = Record<string, string | null>;

// Curated lists — match the IDs the voice-agent recognizes in
// SPEECH_MODELS / CLI_MODELS. If a value lands in state.db that
// isn't in the list, we still display it (free-form) but mark it
// as "custom".
const VOICE_MODEL_OPTIONS = [
  "llama-3.3-70b-versatile",
  "llama-3.1-8b-instant",
  "deepseek-chat",
  "qwen3-32b",
  "openai/gpt-oss-120b",
];
const CLI_MODEL_OPTIONS = [
  "deepseek-v4-pro",
  "deepseek-v4-flash",
  "qwen/qwen3-32b",
  "llama-3.3-70b-versatile",
  "meta-llama/llama-4-scout-17b-16e-instruct",
  "openai/gpt-oss-120b",
];
const TTS_PROVIDER_OPTIONS = [
  "groq:troy",
  "groq:tara",
  "groq:leo",
  "groq:zac",
  "groq:zoe",
  "groq:dan",
];

export function VoiceAndModelsSection() {
  const [settings, setSettings] = useState<HubSettings>({});
  const [loading, setLoading] = useState(true);

  // Initial fetch
  useEffect(() => {
    fetch("/api/hub-settings")
      .then((r) => r.json())
      .then((data: HubSettings) => {
        setSettings(data);
        setLoading(false);
      })
      .catch((e) => {
        toast.error(`Couldn't load hub settings: ${e}`);
        setLoading(false);
      });
  }, []);

  // Live updates via SSE
  useEffect(() => {
    const es = new EventSource("/api/events/stream/settings");
    es.onmessage = (msg) => {
      try {
        const evt = JSON.parse(msg.data);
        if (evt.type !== "settings.value.changed") return;
        const { key, value } = evt.payload;
        if (typeof key === "string" && typeof value === "string") {
          setSettings((prev) => ({ ...prev, [key]: value }));
        }
      } catch {
        /* malformed line — drop */
      }
    };
    return () => es.close();
  }, []);

  const update = async (key: string, value: string) => {
    try {
      const r = await fetch(`/api/hub-settings?key=${encodeURIComponent(key)}`, {
        method: "PUT",
        body: value,
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({ error: "unknown" }));
        toast.error(`Update failed: ${err.error ?? r.status}`);
        return;
      }
      // Optimistic — SSE will overwrite if the watcher's read differs.
      setSettings((prev) => ({ ...prev, [key]: value }));
      toast.success(`${key} → ${value}`);
    } catch (e) {
      toast.error(`Network error: ${String(e)}`);
    }
  };

  if (loading) {
    return (
      <div className="space-y-6">
        <h2 className="text-lg font-semibold">Voice &amp; Models</h2>
        <p className="text-sm text-muted-foreground">Loading…</p>
      </div>
    );
  }

  const renderRow = (
    label: string,
    description: string,
    key: string,
    options: string[],
  ) => {
    const current = settings[key] ?? "";
    const isCustom = current && !options.includes(current);
    return (
      <div className="grid grid-cols-[180px_1fr] items-start gap-x-6 gap-y-1 py-3">
        <div>
          <div className="text-sm font-medium">{label}</div>
          <div className="text-xs text-muted-foreground">{description}</div>
        </div>
        <div className="space-y-1">
          <Select
            value={current || undefined}
            onValueChange={(v) => update(key, v)}
          >
            <SelectTrigger className="w-full max-w-md">
              <SelectValue placeholder={`Choose ${label.toLowerCase()}…`} />
            </SelectTrigger>
            <SelectContent>
              {options.map((opt) => (
                <SelectItem key={opt} value={opt}>
                  {opt}
                </SelectItem>
              ))}
              {isCustom && (
                <SelectItem value={current}>{current} (custom)</SelectItem>
              )}
            </SelectContent>
          </Select>
          <div className="text-xs text-muted-foreground font-mono">
            {current || "—"}{" "}
            {isCustom && (
              <span className="text-amber-600">(not in curated list)</span>
            )}
          </div>
        </div>
      </div>
    );
  };

  return (
    <div className="space-y-1">
      <h2 className="text-lg font-semibold">Voice &amp; Models</h2>
      <p className="pb-3 text-sm text-muted-foreground">
        Cross-cutting settings shared with the voice agent and CLI.
        Backed by the JARVIS hub at{" "}
        <code className="text-xs">~/.jarvis/hub/state.db</code>; changes
        propagate live to all subsystems within ~1 s. API keys are managed
        separately by the desktop tray.
      </p>

      <div className="divide-y divide-border/40">
        {renderRow(
          "Voice model",
          "LLM the voice agent uses for speech replies. Restart-on-change.",
          "voice-model",
          VOICE_MODEL_OPTIONS,
        )}
        {renderRow(
          "CLI model",
          "Default model for the JARVIS CLI's planner and agentic loops.",
          "cli-model",
          CLI_MODEL_OPTIONS,
        )}
        {renderRow(
          "TTS provider",
          "Voice + provider for spoken output. Format: provider:voice.",
          "tts-provider",
          TTS_PROVIDER_OPTIONS,
        )}
      </div>
    </div>
  );
}
