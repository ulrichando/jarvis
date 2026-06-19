"use client";

import { useCallback, useState } from "react";
import { toast } from "sonner";
import {
  AlertCircle,
  Check,
  Download,
  Loader2,
  RefreshCw,
  Server,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SettingsSection } from "./field";
import { useSettings, useUpdateSettings } from "@/hooks/use-settings";

type OllamaModel = {
  name: string;
  size?: number;
  modified?: string;
  family?: string;
  parameterSize?: string;
};

type DetectResult = {
  ok: boolean;
  baseURL: string;
  version?: string;
  models?: OllamaModel[];
  error?: string;
};

function fmtSize(bytes?: number): string {
  if (!bytes) return "";
  const gb = bytes / 1024 ** 3;
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.round(bytes / 1024 ** 2)} MB`;
}

/**
 * Ollama connection + model management — JARVIS's take on Open WebUI's
 * Settings → Connections + Models. Configure the base URL, detect installed
 * models, and pull new ones (streamed progress). Pulled models land in the
 * SAME Ollama the voice-agent's local LLM runs against.
 */
export function OllamaConnection() {
  const { data } = useSettings();
  const update = useUpdateSettings();

  const storedURL = data?.connections?.ollama?.baseURL ?? "";
  const [url, setUrl] = useState(storedURL);
  const [detect, setDetect] = useState<DetectResult | null>(null);
  const [detecting, setDetecting] = useState(false);

  const [pullName, setPullName] = useState("");
  const [pullStatus, setPullStatus] = useState<string | null>(null);
  const [pullPct, setPullPct] = useState<number | null>(null);
  const [pulling, setPulling] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setDetecting(true);
    try {
      const res = await fetch("/api/ollama/models", { cache: "no-store" });
      setDetect((await res.json()) as DetectResult);
    } catch (e) {
      setDetect({
        ok: false,
        baseURL: url || "http://127.0.0.1:11434",
        error: e instanceof Error ? e.message : "request failed",
      });
    } finally {
      setDetecting(false);
    }
  }, [url]);

  const save = useCallback(async () => {
    const trimmed = url.trim();
    try {
      await update.mutateAsync({
        connections: { ollama: { baseURL: trimmed === "" ? null : trimmed } },
      });
      toast.success("Ollama URL saved");
      refresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "save failed");
    }
  }, [url, update, refresh]);

  const pull = useCallback(async () => {
    const name = pullName.trim();
    if (!name) return;
    setPulling(true);
    setPullStatus("starting…");
    setPullPct(null);
    try {
      const res = await fetch("/api/ollama/pull", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (!res.ok || !res.body) {
        const err = (await res.json().catch(() => ({}))) as { error?: string };
        throw new Error(err.error ?? `pull failed (${res.status})`);
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.trim()) continue;
          let evt: { status?: string; completed?: number; total?: number; error?: string };
          try {
            evt = JSON.parse(line);
          } catch {
            continue; // partial line — wait for more
          }
          if (evt.error) throw new Error(evt.error);
          if (evt.status) setPullStatus(evt.status);
          if (evt.total && evt.completed != null) {
            setPullPct(Math.round((evt.completed / evt.total) * 100));
          }
        }
      }
      setPullStatus("done");
      toast.success(`Pulled ${name}`);
      setPullName("");
      refresh();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "pull failed";
      setPullStatus(`error: ${msg}`);
      toast.error(msg);
    } finally {
      setPulling(false);
    }
  }, [pullName, refresh]);

  const del = useCallback(
    async (name: string) => {
      if (!window.confirm(`Delete ${name}? You'd have to re-pull it to get it back.`)) {
        return;
      }
      setDeleting(name);
      try {
        const res = await fetch(
          `/api/ollama/models?name=${encodeURIComponent(name)}`,
          { method: "DELETE" },
        );
        if (!res.ok) {
          const err = (await res.json().catch(() => ({}))) as { error?: string };
          throw new Error(err.error ?? `delete failed (${res.status})`);
        }
        toast.success(`Deleted ${name}`);
        refresh();
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "delete failed");
      } finally {
        setDeleting(null);
      }
    },
    [refresh],
  );

  const models = detect?.models ?? [];

  return (
    <SettingsSection
      title="Local models — Ollama"
      description="Connect to an Ollama server to detect + pull local models. Pulled models land in the same server the voice agent's local LLM uses (JARVIS_LOCAL_LLM_URL)."
    >
      {/* Base URL + verify */}
      <div className="flex items-center gap-2">
        <Server className="h-4 w-4 shrink-0 text-muted-foreground" />
        <Input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="http://127.0.0.1:11434"
          className="flex-1"
          spellCheck={false}
        />
        <Button onClick={save} disabled={update.isPending} size="sm">
          {update.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : "Save"}
        </Button>
        <Button onClick={refresh} disabled={detecting} variant="outline" size="sm">
          {detecting ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <RefreshCw className="h-4 w-4" />
          )}
        </Button>
      </div>

      {/* Connection status */}
      {detect && (
        <p className="flex items-center gap-1.5 text-xs">
          {detect.ok ? (
            <>
              <Check className="h-3.5 w-3.5 text-green-500" />
              Connected — Ollama {detect.version} @ {detect.baseURL}
            </>
          ) : (
            <>
              <AlertCircle className="h-3.5 w-3.5 text-destructive" />
              Not connected ({detect.error}) @ {detect.baseURL}
            </>
          )}
        </p>
      )}

      {/* Installed models */}
      {detect?.ok && (
        <div className="divide-y divide-border/60 rounded-md border border-border/60">
          {models.length === 0 ? (
            <p className="px-3 py-2 text-xs text-muted-foreground">
              No models installed yet — pull one below.
            </p>
          ) : (
            models.map((m) => (
              <div
                key={m.name}
                className="flex items-center justify-between px-3 py-2 text-sm"
              >
                <span className="font-medium">{m.name}</span>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground">
                    {[m.parameterSize, fmtSize(m.size)].filter(Boolean).join(" · ")}
                  </span>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => del(m.name)}
                    disabled={deleting === m.name}
                    aria-label={`Delete ${m.name}`}
                    className="h-7 w-7 p-0 text-muted-foreground hover:text-destructive"
                  >
                    {deleting === m.name ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Trash2 className="h-3.5 w-3.5" />
                    )}
                  </Button>
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {/* Pull a model */}
      <div className="flex items-center gap-2">
        <Input
          value={pullName}
          onChange={(e) => setPullName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void pull();
          }}
          placeholder="Pull a model, e.g. llama3.2:3b or qwen2.5-coder:7b"
          className="flex-1"
          spellCheck={false}
          disabled={pulling}
        />
        <Button onClick={pull} disabled={pulling || !pullName.trim()} size="sm">
          {pulling ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <>
              <Download className="mr-1 h-4 w-4" />
              Pull
            </>
          )}
        </Button>
      </div>

      {pullStatus && (
        <div className="text-xs text-muted-foreground">
          {pullStatus}
          {pullPct != null ? ` — ${pullPct}%` : ""}
          {pullPct != null && (
            <div className="mt-1 h-1.5 w-full overflow-hidden rounded bg-muted">
              <div
                className="h-full bg-primary transition-all"
                style={{ width: `${pullPct}%` }}
              />
            </div>
          )}
        </div>
      )}

      <p className="text-[11px] leading-4 text-muted-foreground">
        Browse model tags at{" "}
        <a
          href="https://ollama.com/library"
          target="_blank"
          rel="noreferrer"
          className="underline"
        >
          ollama.com/library
        </a>
        .
      </p>
    </SettingsSection>
  );
}
