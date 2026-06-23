"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { CuAppBar, type ConnStatus } from "@/components/computer-use/app-bar";
import { DesktopStage, type Status } from "@/components/computer-use/desktop-stage";
import { ActivityTimeline } from "@/components/computer-use/activity-timeline";
import { CommandBar } from "@/components/computer-use/command-bar";
import { CU_MODELS } from "@/components/computer-use/model-picker";
import type { NoVNCHandle } from "@/components/computer-use/novnc-view";
import { eventToPart, type ChatMsg, type Part, type LoopEvent } from "@/lib/computer-use/timeline";

const newSessionId = () => (typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : String(Date.now()));

export default function ComputerUsePage() {
  const [status, setStatus] = useState<Status | null>(null);
  const [vnc, setVnc] = useState<"connecting" | "connected" | "disconnected">("connecting");
  const [task, setTask] = useState("");
  const [running, setRunning] = useState(false);
  const [takeover, setTakeover] = useState(false);
  const [connected, setConnected] = useState(true);
  const [supervised, setSupervised] = useState(true);
  const [model, setModel] = useState<string>(CU_MODELS[0].id);
  const [thread, setThread] = useState<ChatMsg[]>([]);
  const [sessionId, setSessionId] = useState("");
  const [runStart, setRunStart] = useState<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const novncRef = useRef<NoVNCHandle | null>(null);

  const refreshStatus = useCallback(async () => {
    try {
      const r = await fetch("/api/computer-use", { cache: "no-store" });
      setStatus((await r.json()) as Status);
    } catch {
      setStatus({ ready: false, streamUp: false, sidecarUp: false, wsUrl: "", password: null, hint: "Could not reach the web API." });
    }
  }, []);
  useEffect(() => { void refreshStatus(); }, [refreshStatus]);

  // Session id is generated client-only: crypto.randomUUID() is non-deterministic,
  // so producing it during SSR (then displaying it in the app-bar chip) mismatches
  // on hydration. Empty on the server + first client render, then filled on mount.
  useEffect(() => { setSessionId((id) => id || newSessionId()); }, []);

  const appendPart = useCallback((part: Part) => {
    setThread((prev) => {
      if (prev.length === 0) return prev;
      const last = prev[prev.length - 1];
      if (last.role !== "assistant") return prev;
      const copy = prev.slice();
      copy[copy.length - 1] = { ...last, parts: [...last.parts, part] };
      return copy;
    });
  }, []);

  const stop = useCallback(() => { abortRef.current?.abort(); abortRef.current = null; setRunning(false); }, []);
  const takeControl = useCallback(() => { stop(); setTakeover(true); }, [stop]);
  const newChat = useCallback(() => { stop(); setThread([]); setSessionId(newSessionId()); }, [stop]);
  const disconnect = useCallback(() => { stop(); setConnected(false); setVnc("disconnected"); }, [stop]);
  const connect = useCallback(() => { setVnc("connecting"); setConnected(true); }, []);

  const resolvePermission = useCallback(async (reqId: string, decision: "once" | "session" | "deny") => {
    setThread((prev) => prev.map((m) => ({ ...m, parts: m.parts.map((p) => (p.kind === "permission" && p.reqId === reqId ? { ...p, resolved: decision } : p)) })));
    try {
      await fetch("/api/computer-use/approve", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ request_id: reqId, decision }) });
    } catch { /* loop times out -> denies */ }
  }, []);

  const runTask = useCallback(async (override?: string) => {
    const t = (override ?? task).trim();
    if (!t || running || !status?.ready) return;
    setTakeover(false); setRunning(true); setTask(""); setRunStart(Date.now());
    setThread((prev) => [...prev, { role: "user", parts: [{ kind: "text", text: t }] }, { role: "assistant", parts: [] }]);
    const ctrl = new AbortController(); abortRef.current = ctrl;
    try {
      const res = await fetch("/api/computer-use", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ task: t, session_id: sessionId, supervised, model }), signal: ctrl.signal });
      if (!res.body) throw new Error("no stream");
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let idx: number;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const frame = buf.slice(0, idx); buf = buf.slice(idx + 2);
          const dataLine = frame.split("\n").find((l) => l.startsWith("data:"));
          if (!dataLine) continue;
          let evt: LoopEvent;
          try { evt = JSON.parse(dataLine.slice(5).trim()) as LoopEvent; } catch { continue; }
          const part = eventToPart(evt, Date.now());
          if (!part) continue;
          if (part.kind === "action") part.thumb = novncRef.current?.snapshot() ?? undefined;
          appendPart(part);
        }
      }
    } catch (err) {
      if (!ctrl.signal.aborted) appendPart({ kind: "error", text: err instanceof Error ? err.message : "run failed", ts: Date.now() });
    } finally {
      if (abortRef.current === ctrl) abortRef.current = null;
      setRunning(false);
    }
  }, [task, running, status?.ready, sessionId, supervised, model, appendPart]);

  const connStatus: ConnStatus = !status?.ready ? "offline" : connected ? vnc : "disconnected";
  const placeholder = running ? "Working… press Stop to interrupt" : takeover ? "You're in control of the desktop" : "Tell Jarvis what to do on the desktop…";

  return (
    <div className="flex h-full flex-col">
      <CuAppBar
        connStatus={connStatus} sessionId={sessionId} supervised={supervised} takeover={takeover}
        connected={connected} running={running} hasThread={thread.length > 0}
        onToggleMode={() => setSupervised((v) => !v)}
        onToggleTakeover={takeover ? () => setTakeover(false) : takeControl}
        onToggleConnected={connected ? disconnect : connect}
        onNewChat={newChat} onStop={stop} onRefresh={() => void refreshStatus()}
      />
      <div className="flex min-h-0 flex-1">
        <DesktopStage
          status={status} connected={connected} takeover={takeover} running={running} novncRef={novncRef}
          onTakeControl={takeControl} onGiveControl={() => setTakeover(false)} onConnect={connect} onRecheck={() => void refreshStatus()} onVncState={setVnc}
        />
        <ActivityTimeline
          thread={thread} running={running} runStart={runStart} ready={!!status?.ready}
          onApprove={resolvePermission} onRunExample={(ex) => void runTask(ex)}
        />
      </div>
      <CommandBar
        value={task} onChange={setTask} onSubmit={() => void runTask()} running={running}
        disabled={!status?.ready || running || takeover} model={model} setModel={setModel}
        providers={status?.providers} placeholder={placeholder}
      />
    </div>
  );
}
