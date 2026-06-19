"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Check, Eye, EyeOff, Loader2, Trash2, Zap } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SettingsSection } from "./field";
import { OllamaConnection } from "./ollama-connection";
import { ProviderDot } from "@/components/layout/provider-dot";
import {
  useSettings,
  useTestProvider,
  useUpdateSettings,
} from "@/hooks/use-settings";
import { PROVIDER_LABEL, type Provider } from "@/lib/ai/models-meta";

const PROVIDERS: Array<{
  id: Provider;
  docs: string;
  help: string;
  supportsBaseURL?: boolean;
}> = [
  {
    id: "anthropic",
    docs: "https://console.anthropic.com/settings/keys",
    help: "sk-ant-… — Anthropic console",
  },
  {
    id: "openai",
    docs: "https://platform.openai.com/api-keys",
    help: "sk-… — OpenAI platform",
    supportsBaseURL: true,
  },
  {
    id: "google",
    docs: "https://aistudio.google.com/apikey",
    help: "AIza… — Google AI Studio",
  },
  {
    id: "groq",
    docs: "https://console.groq.com/keys",
    help: "gsk_… — Groq console (free tier)",
  },
  {
    id: "deepseek",
    docs: "https://platform.deepseek.com/api_keys",
    help: "sk-… — DeepSeek platform",
  },
  {
    id: "kimi",
    docs: "https://platform.moonshot.ai/console/api-keys",
    help: "sk-… — Moonshot console",
    supportsBaseURL: true,
  },
];

export function ProvidersSection() {
  const { data } = useSettings();
  if (!data) return <p className="text-sm text-muted-foreground">Loading…</p>;

  return (
    <>
      <SettingsSection
        description="Keys are stored locally in .jarvis/settings.json. Empty = falls back to process env."
      >
        <div className="space-y-3">
          {PROVIDERS.map((p) => (
            <ProviderRow key={p.id} provider={p} />
          ))}
        </div>
      </SettingsSection>
      <OllamaConnection />
    </>
  );
}

function ProviderRow({
  provider,
}: {
  provider: (typeof PROVIDERS)[number];
}) {
  const { data } = useSettings();
  const update = useUpdateSettings();
  const test = useTestProvider();

  const stored = data?.providers[provider.id];
  const [key, setKey] = useState("");
  const [baseURL, setBaseURL] = useState(stored?.baseURL ?? "");
  const [show, setShow] = useState(false);

  const hasStoredKey = stored?.hasKey;

  const save = async () => {
    if (!key.trim() && !provider.supportsBaseURL) return;
    try {
      await update.mutateAsync({
        providers: {
          [provider.id]: {
            ...(key.trim() ? { apiKey: key.trim() } : {}),
            ...(provider.supportsBaseURL
              ? { baseURL: baseURL.trim() || null }
              : {}),
          },
        },
      });
      setKey("");
      toast.success(`${PROVIDER_LABEL[provider.id]} saved`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Save failed");
    }
  };

  const clear = async () => {
    try {
      await update.mutateAsync({
        providers: { [provider.id]: { apiKey: null } },
      });
      setKey("");
      toast.success(`${PROVIDER_LABEL[provider.id]} key removed`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Remove failed");
    }
  };

  const runTest = async () => {
    const result = await test.mutateAsync(provider.id);
    if (result.ok) {
      toast.success(
        `${PROVIDER_LABEL[provider.id]} ok · ${result.latencyMs}ms`,
      );
    } else {
      toast.error(result.error ?? "Test failed");
    }
  };

  return (
    <div className="rounded-md border border-border/60 bg-background/40 p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <ProviderDot provider={provider.id} />
          <span className="text-sm font-medium">
            {PROVIDER_LABEL[provider.id]}
          </span>
          {hasStoredKey ? (
            <span className="flex items-center gap-1 rounded-full border border-primary/40 bg-primary/10 px-2 py-0.5 font-mono text-[10px] text-primary">
              <Check className="size-2.5" />
              {stored.keyPreview}
            </span>
          ) : (
            <span className="rounded-full border border-border/60 px-2 py-0.5 font-mono text-[10px] text-muted-foreground">
              not set
            </span>
          )}
        </div>
        <a
          href={provider.docs}
          target="_blank"
          rel="noreferrer"
          className="text-[11px] text-muted-foreground underline-offset-4 hover:text-primary hover:underline"
        >
          get a key
        </a>
      </div>

      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Input
            type={show ? "text" : "password"}
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder={hasStoredKey ? "replace key…" : provider.help}
            className="pr-9 font-mono text-xs"
          />
          <button
            type="button"
            onClick={() => setShow((s) => !s)}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
            aria-label={show ? "Hide" : "Show"}
          >
            {show ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
          </button>
        </div>
        <Button
          size="sm"
          onClick={save}
          disabled={update.isPending || (!key.trim() && !provider.supportsBaseURL)}
        >
          Save
        </Button>
        {hasStoredKey && (
          <>
            <Button
              size="sm"
              variant="outline"
              onClick={runTest}
              disabled={test.isPending}
            >
              {test.isPending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Zap className="size-3.5" />
              )}
              Test
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={clear}
              disabled={update.isPending}
              aria-label="Remove key"
              className="text-muted-foreground hover:text-destructive"
            >
              <Trash2 className="size-3.5" />
            </Button>
          </>
        )}
      </div>

      {provider.supportsBaseURL && (
        <div className="mt-2">
          <Input
            value={baseURL}
            onChange={(e) => setBaseURL(e.target.value)}
            placeholder="custom base URL (optional)"
            className="font-mono text-xs"
          />
        </div>
      )}
    </div>
  );
}
