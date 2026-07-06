"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { CuAppBar, type ConnStatus } from "@/components/computer-use/app-bar";
import { DesktopStage, type Status } from "@/components/computer-use/desktop-stage";
import { ActivityTimeline } from "@/components/computer-use/activity-timeline";
import { CommandBar } from "@/components/computer-use/command-bar";
import { CU_MODELS } from "@/components/computer-use/model-picker";
import type { NoVNCHandle } from "@/components/computer-use/novnc-view";
import { applyEvent, type ChatMsg, type LoopEvent, type SessionInfo } from "@/lib/computer-use/timeline";

const newSessionId = () => (typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : String(Date.now()));
const SUPERVISED_KEY = "jarvis.cu.supervised";

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
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
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

  // Approval-mode preference persists across visits (issue: Auto silently reset
  // to Supervised on every page load). localStorage is read post-mount for the
  // same SSR-hydration reason as the session id.
  useEffect(() => {
    try { const v = localStorage.getItem(SUPERVISED_KEY); if (v !== null) setSupervised(v === "1"); } catch { /* private mode */ }
  }, []);

  // One consumer for BOTH the live run stream and the reattach replay, folding
  // frames into the thread via applyEvent so the two paths can't drift.
  // withThumbs is off for replays — snapshotting the CURRENT desktop would
  // stamp historical action rows with a frame from the wrong moment.
  const consumeStream = useCallback(async (res: Response, handleStart: boolean, withThumbs: boolean) => {
    if (!res.body) throw new Error("no stream");
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    const snap = withThumbs ? () => novncRef.current?.snapshot() ?? undefined : undefined;
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
        setThread((prev) => applyEvent(prev, evt, Date.now(), handleStart, snap));
      }
    }
  }, []);

  const loadSessions = useCallback(async () => {
    try {
      const r = await fetch("/api/computer-use/sessions", { cache: "no-store" });
      const data = (await r.json()) as { sessions?: SessionInfo[] };
      setSessions(data.sessions ?? []);
      return data.sessions ?? [];
    } catch { return []; }
  }, []);

  // Detach from the current stream WITHOUT stopping the run (it's detached
  // sidecar-side; still visible under Sessions).
  const detach = useCallback(() => { abortRef.current?.abort(); abortRef.current = null; setRunning(false); }, []);

  // Client-side navigation away must not leak the SSE reader.
  useEffect(() => () => { abortRef.current?.abort(); abortRef.current = null; }, []);

  // Explicit stop: with detached runs, closing the page no longer kills
  // anything — this button is the intentional kill. The stream stays attached
  // so the run's terminal `stopped` event lands as a visible row; detach() is
  // only the fallback when the stop can't reach the sidecar.
  const stop = useCallback(() => {
    if (!sessionId) { detach(); return; }
    void fetch("/api/computer-use/stop", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    }).then((r) => { if (!r.ok) detach(); }).catch(() => detach());
  }, [sessionId, detach]);

  // Rebuild the thread from a session's buffered events, then follow live.
  const attachSession = useCallback(async (s: SessionInfo) => {
    detach();
    setSessionId(s.id);
    setThread([]);
    setTakeover(false);
    // Reflect the adopted session's ACTUAL mode, not the local preference.
    if (typeof s.supervised === "boolean") setSupervised(s.supervised);
    const live = s.status === "running" || s.status === "waiting_approval";
    setRunning(live);
    if (live) setRunStart(Date.now());
    const ctrl = new AbortController(); abortRef.current = ctrl;
    try {
      const res = await fetch(`/api/computer-use/events?session_id=${encodeURIComponent(s.id)}&after=-1`, { signal: ctrl.signal });
      if (res.ok) await consumeStream(res, true, false);
    } catch { /* aborted or sidecar gone — thread keeps whatever replayed */ }
    finally {
      // Identity-guarded: if another stream replaced this one (new run / new
      // attach), ITS lifecycle owns `running` — don't flip it false underneath.
      if (abortRef.current === ctrl) { abortRef.current = null; setRunning(false); }
    }
  }, [detach, consumeStream]);

  // On load, adopt a still-running session (the "is it even still working after
  // I closed the browser?" case) — replay its history and follow live.
  const didAdopt = useRef(false);
  useEffect(() => {
    if (!didAdopt.current) {
      didAdopt.current = true;
      void loadSessions().then((list) => {
        const live = list.find((s) => s.status === "running" || s.status === "waiting_approval");
        if (live) void attachSession(live);
      });
    }
    // StrictMode dev remount: the unmount cleanup aborts the first adoption's
    // stream, so re-arm the flag for the second mount to adopt again.
    return () => { didAdopt.current = false; };
  }, [loadSessions, attachSession]);

  const takeControl = useCallback(() => { stop(); setTakeover(true); }, [stop]);
  // New session: start a fresh thread; a detached run keeps going (Sessions lists it).
  const newChat = useCallback(() => { detach(); setThread([]); setSessionId(newSessionId()); }, [detach]);
  const disconnect = useCallback(() => { detach(); setConnected(false); setVnc("disconnected"); }, [detach]);
  const connect = useCallback(() => { setVnc("connecting"); setConnected(true); }, []);

  // Mode changes persist AND apply to the running session immediately —
  // flipping to Auto releases any approval the run is currently waiting on.
  const toggleMode = useCallback(() => {
    const next = !supervised;
    setSupervised(next);
    try { localStorage.setItem(SUPERVISED_KEY, next ? "1" : "0"); } catch { /* private mode */ }
    if (sessionId) {
      void fetch("/api/computer-use/mode", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, supervised: next }),
      }).catch(() => {});
    }
  }, [supervised, sessionId]);

  const resolvePermission = useCallback(async (reqId: string, decision: "once" | "session" | "deny") => {
    setThread((prev) => prev.map((m) => ({ ...m, parts: m.parts.map((p) => (p.kind === "permission" && p.reqId === reqId ? { ...p, resolved: decision } : p)) })));
    try {
      await fetch("/api/computer-use/approve", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ request_id: reqId, decision }) });
    } catch { /* loop times out -> denies */ }
  }, []);

  const runTask = useCallback(async (override?: string) => {
    const t = (override ?? task).trim();
    if (!t || running || !status?.ready) return;
    detach(); // drop any reattach stream before opening the run stream
    setTakeover(false); setRunning(true); setTask(""); setRunStart(Date.now());
    setThread((prev) => [...prev, { role: "user", parts: [{ kind: "text", text: t }] }, { role: "assistant", parts: [] }]);
    const ctrl = new AbortController(); abortRef.current = ctrl;
    try {
      const res = await fetch("/api/computer-use", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ task: t, session_id: sessionId, supervised, model }), signal: ctrl.signal });
      await consumeStream(res, false, true);
    } catch (err) {
      if (!ctrl.signal.aborted) setThread((prev) => applyEvent(prev, { type: "error", error: err instanceof Error ? err.message : "run failed" }, Date.now(), false));
    } finally {
      // Identity-guarded (see attachSession) — a replaced stream must not
      // flip `running` false while the replacement is live.
      if (abortRef.current === ctrl) { abortRef.current = null; setRunning(false); }
    }
  }, [task, running, status?.ready, sessionId, supervised, model, detach, consumeStream]);

  const connStatus: ConnStatus = !status?.ready ? "offline" : connected ? vnc : "disconnected";
  const placeholder = running ? "Working… press Stop to interrupt" : takeover ? "You're in control of the desktop" : "Tell Jarvis what to do on the desktop…";

  return (
    <div className="flex h-full flex-col">
      <CuAppBar
        connStatus={connStatus} sessionId={sessionId} supervised={supervised} takeover={takeover}
        connected={connected} running={running} hasThread={thread.length > 0}
        sessions={sessions} onLoadSessions={() => void loadSessions()} onPickSession={(s) => void attachSession(s)}
        onToggleMode={toggleMode}
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
