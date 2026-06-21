"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useParams } from "next/navigation";
import { Asterisk, ChevronRight, X, Shield, ExternalLink, Check, PanelLeftOpen } from "lucide-react";

// Session id ⇄ URL path. We display claude's shape (/code/session_<id>);
// internally session ids are bare. Prefix on write, strip on read.
const SESSION_PREFIX = "session_";
const toPath = (id: string) => `/code/${SESSION_PREFIX}${id}`;
const fromSeg = (seg: string | undefined) =>
  seg ? seg.replace(new RegExp(`^${SESSION_PREFIX}`), "") : null;
import { CodeSidebar } from "@/components/code/code-sidebar";
import { CodeComposer, type Attachment } from "@/components/code/code-composer";
import { CodeSession } from "@/components/code/code-session";
import { CodePanels, type PanelName } from "@/components/code/code-panels";
import { RoutinesView } from "@/components/code/routines-view";

type Machine = {
  environment_id: string;
  machine_name: string;
  directory: string;
  branch: string | null;
  git_repo_url: string | null;
  worker_type: string;
  last_seen_at: number;
  online: boolean;
};

type SessionSummary = {
  session_id: string;
  environment_id?: string | null;
  title: string;
  preview: string;
  repo: string | null;
  machine_name: string | null;
  created_at: number;
  status: "needs_input" | "working" | "done";
  pinned?: boolean;
  read?: boolean;
  archived?: boolean;
  group_id?: string | null;
  group_name?: string | null;
};

function repoLabel(m: Machine | null): string | null {
  if (!m) return null;
  if (m.git_repo_url) {
    const s = m.git_repo_url.replace(/\.git$/, "").split("/");
    return s.slice(-2).join("/") || (s.slice(-1)[0] ?? null);
  }
  return m.directory.split("/").filter(Boolean).slice(-1)[0] ?? null;
}

function timeAgo(ts: number): string {
  const s = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d`;
  return `${Math.floor(d / 7)}w`;
}

const STATUS_META: Record<SessionSummary["status"], { dot: string; label: string; text: string }> = {
  needs_input: { dot: "bg-amber-500", label: "Needs input", text: "text-amber-500/90" },
  working: { dot: "bg-blue-500 animate-pulse", label: "Working", text: "text-blue-500/90" },
  done: { dot: "bg-muted-foreground/40", label: "Done", text: "text-muted-foreground" },
};

export default function CodePage() {
  const [input, setInput] = useState("");
  // Permission mode for dispatch + live switching (ExternalPermissionMode).
  // Default to autonomous (like bin/jarvis + claude.ai/code cloud sessions):
  // the container is the isolation boundary, so the agent should install deps,
  // run code, commit and push without a permission prompt for every command.
  const [mode, setMode] = useState("bypassPermissions");
  // Selected model (MODELS_META id) — applied at dispatch + live via set_model.
  // Default to the jarvis CLI's default model (DeepSeek v4 Pro), so a web
  // /code session matches `bin/jarvis` out of the box. The picker can switch.
  const [model, setModel] = useState("deepseek-v4-pro");
  // Restore the persisted model pick AFTER mount. Reading localStorage in
  // the useState initializer would desync SSR (default) vs client → a
  // hydration mismatch on the toolbar label; an effect is hydration-safe.
  // Saved by changeModel below so the pick survives a reload (the page
  // state otherwise re-initialises to the default every mount).
  useEffect(() => {
    const saved = window.localStorage.getItem("jarvis:code:model");
    if (saved) setModel(saved);
  }, []);
  // Per-session MCP connectors (opt-in). Default empty: nothing auto-attaches —
  // the user ticks only what a given task needs. (GitHub is separate, handled by
  // the repo picker, not an MCP connector.) Deliberately NOT persisted: every
  // new session starts a clean slate, so an unrelated session never silently
  // re-attaches a connector used in a previous one.
  const [connectors, setConnectors] = useState<string[]>([]);
  const [availableConnectors, setAvailableConnectors] = useState<{ id: string; name: string }[]>([]);
  useEffect(() => {
    // Offer only enabled, container-capable connectors. stdio servers are
    // skipped — their binary isn't inside the container (launch skips them too).
    fetch("/api/mcp")
      .then((r) => (r.ok ? r.json() : { servers: [] }))
      .then((j: { servers?: { id: string; name: string; enabled: boolean; transport: string }[] }) =>
        setAvailableConnectors(
          (j.servers ?? [])
            .filter((s) => s.enabled && s.transport !== "stdio")
            .map((s) => ({ id: s.id, name: s.name })),
        ),
      )
      .catch(() => {});
  }, []);
  // GitHub repo picked in the composer → tasks run in a cloud container.
  const [cloudRepo, setCloudRepo] = useState<string | null>(null);
  // Pending image attachments (base64) for the next send.
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  // Whether the open session's worker is running (→ composer shows Stop).
  const [running, setRunning] = useState(false);

  const stopSession = () => {
    if (!sessionId) return;
    fetch(`/api/bridge/v1/sessions/${sessionId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ interrupt: true }),
    }).catch(() => {});
  };
  const [machines, setMachines] = useState<Machine[] | null>(null);
  const [selected, setSelected] = useState<Machine | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  // Routines view (path /code/routines) — distinct from a session.
  const [showRoutines, setShowRoutines] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A 401 from a write = the login session lapsed server-side. Show a re-login
  // prompt (keeps the typed draft) instead of a silent "send failed".
  const [authExpired, setAuthExpired] = useState(false);
  // Bumped on each send to the open session so CodeSession shows the thinking
  // indicator + fast-polls immediately (no "nothing, then sudden" dead window).
  const [sendNonce, setSendNonce] = useState(0);
  // Draggable width of the left sidebar (px). Persisted across reloads.
  const [sidebarWidth, setSidebarWidth] = useState(260);
  // Collapse the session sidebar (like the main app's "Jarvis" sidebar).
  const [codeSidebarOpen, setCodeSidebarOpen] = useState(true);
  // Inline review comments queued from the diff panel — bundled into the next
  // message with their location, like claude.ai/code.
  const [diffComments, setDiffComments] = useState<{ file: string; line: number; text: string }[]>([]);
  // Additional repos to clone into a new container session (multi-repo). The
  // composer pill row renders these with a remove × + a "+" repo picker.
  const [extraRepos, setExtraRepos] = useState<string[]>([]);
  // Environment config modal (env vars + setup script, claude.ai/code env config).
  const [envCfg, setEnvCfg] = useState<{
    id: string | null; // null in create mode — the env is created on save
    mode: "create" | "edit";
    name: string;
    envText: string;
    setupScript: string;
    networkLevel: string;
    customAllowlist: string;
  } | null>(null);
  const [envCfgBusy, setEnvCfgBusy] = useState(false);
  // Keyboard-shortcuts help overlay (toggled with ?).
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [panels, setPanels] = useState({ diff: false, background: false, plan: false });
  const [shareOpen, setShareOpen] = useState(false);
  const [shareVisibility, setShareVisibility] = useState<"private" | "public">("private");

  const loadMachines = useCallback(async () => {
    try {
      const r = await fetch("/api/bridge/v1/environments");
      if (r.ok) {
        const j = (await r.json()) as { environments: Machine[] };
        setMachines(j.environments);
        // Default the selection to the cloud "Default" env (claude.ai/code web
        // behavior — local machines are attach-only from the web), else a lone env.
        setSelected(
          (cur) =>
            cur ??
            j.environments.find((e) => e.worker_type === "container") ??
            (j.environments.length === 1 ? j.environments[0] : null),
        );
      } else {
        setMachines([]);
      }
    } catch {
      setMachines([]);
    }
  }, []);

  const loadSessions = useCallback(async () => {
    try {
      const r = await fetch("/api/bridge/v1/sessions");
      if (r.ok) {
        const j = (await r.json()) as { sessions: SessionSummary[] };
        setSessions(j.sessions);
      }
    } catch {
      /* keep prior */
    }
  }, []);

  useEffect(() => {
    loadMachines();
    loadSessions();
  }, [loadMachines, loadSessions]);

  // Live-refresh the machine + session lists so a CLI that connects via
  // Remote Control (`/remote-control`) shows up without a manual page reload.
  // Two triggers: tab focus/visibility (instant when you switch back to the
  // browser after running the command) and a slow interval (covers having the
  // terminal and browser open side by side). The interval skips work while the
  // tab is hidden so an idle background tab isn't polling.
  useEffect(() => {
    const refresh = () => {
      loadMachines();
      loadSessions();
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") refresh();
    };
    const id = setInterval(onVisible, 6000);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(id);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [loadMachines, loadSessions]);

  // Path-based session URL (/code/session_<id>, like claude). Restore from the
  // path on load, then keep the URL in sync as the session changes — linkable
  // and survives refresh. First run RESTORES without writing (so it can't strip
  // the incoming id before sessionId catches up → every refresh went to the
  // welcome view). `?s=<id>` is still honored as a legacy fallback.
  const routeParams = useParams();
  const urlSynced = useRef(false);
  useEffect(() => {
    if (!urlSynced.current) {
      urlSynced.current = true;
      const segRaw = routeParams?.session;
      const seg = Array.isArray(segRaw) ? segRaw[0] : segRaw;
      if (seg === "routines") {
        setShowRoutines(true);
        return;
      }
      const fromUrl =
        fromSeg(seg) ?? new URLSearchParams(window.location.search).get("s");
      if (fromUrl && fromUrl !== sessionId) setSessionId(fromUrl);
      return;
    }
    const url = showRoutines ? "/code/routines" : sessionId ? toPath(sessionId) : "/code";
    if (window.location.pathname !== url) {
      window.history.replaceState(null, "", url);
    }
  }, [sessionId, showRoutines, routeParams]);

  const changeMode = (m: string) => {
    setMode(m);
    // Session open → apply live via a set_permission_mode control_request.
    // Otherwise the choice rides the next task dispatch as permission_mode.
    if (sessionId) {
      fetch(`/api/bridge/v1/sessions/${sessionId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: m }),
      }).catch(() => {});
    }
  };

  const changeModel = (id: string) => {
    setModel(id);
    // Persist so the pick survives a reload.
    try {
      window.localStorage.setItem("jarvis:code:model", id);
    } catch {
      /* private mode / storage disabled — non-fatal */
    }
    // Session open → apply live via a set_model control_request; otherwise the
    // choice rides the next task dispatch.
    if (sessionId) {
      fetch(`/api/bridge/v1/sessions/${sessionId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: id }),
      }).catch(() => {});
    }
  };

  const dispatch = async () => {
    setError(null);
    // Allow sending when there's text OR queued inline comments (the comments
    // become the message, claude.ai/code-style).
    if (!input.trim() && diffComments.length === 0 && attachments.length === 0) return;
    // Split attachments: images ride as vision content blocks; other files are
    // inlined into the prompt as fenced text.
    const imagesPayload = attachments
      .filter((a) => a.kind !== "file")
      .map((a) => ({ media_type: a.media_type, data: a.data }));
    const fileBlock = attachments
      .filter((a) => a.kind === "file")
      .map((a) => `Attached file ${a.name}:\n\`\`\`\n${a.text ?? ""}\n\`\`\``)
      .join("\n\n");
    // Session open → the composer messages THAT session (one composer for
    // both modes; the session view has no input of its own). No session →
    // dispatch a new task to the selected machine.
    if (sessionId) {
      setBusy(true);
      // Prepend queued inline comments with their file:line so the agent knows
      // exactly where each note applies.
      const commentBlock = diffComments
        .map((c) => `At ${c.file}:${c.line}, ${c.text}`)
        .join("\n");
      const text = [commentBlock, fileBlock, input.trim()].filter(Boolean).join("\n\n");
      try {
        const r = await fetch(`/api/bridge/v1/sessions/${sessionId}/messages`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text, images: imagesPayload }),
        });
        if (r.ok) {
          setInput("");
          setAttachments([]);
          setDiffComments([]);
          setAuthExpired(false);
          setSendNonce((n) => n + 1);
        } else if (r.status === 401) {
          // Login session lapsed (server fell back to LOCAL). Prompt re-login
          // instead of a silent failure; the draft stays in the composer.
          setAuthExpired(true);
        } else {
          const j = (await r.json().catch(() => ({}))) as { error?: { message?: string } };
          setError(j.error?.message ?? `Send failed (${r.status})`);
        }
      } catch (e) {
        setError(String(e));
      } finally {
        setBusy(false);
      }
      return;
    }
    // Explicit repo pick → cloud-container dispatch: get-or-create the repo's
    // container target (idempotent per user+repo) and run the task in it.
    let environmentId = selected?.environment_id ?? null;
    if (cloudRepo) {
      setBusy(true);
      try {
        const r = await fetch("/api/bridge/v1/environments/cloud", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ repo: cloudRepo }),
        });
        if (!r.ok) {
          const j = (await r.json().catch(() => ({}))) as { error?: { message?: string } };
          setError(j.error?.message ?? `Cloud target failed (${r.status})`);
          setBusy(false);
          return;
        }
        environmentId = ((await r.json()) as { environment_id: string }).environment_id;
        loadMachines();
      } catch (e) {
        setError(String(e));
        setBusy(false);
        return;
      }
    }
    if (!environmentId) {
      setError("Pick a repo (cloud container) or connect a machine — run /remote-control on your machine.");
      setBusy(false);
      return;
    }
    if (selected?.worker_type === "claude_code_repl" && !cloudRepo) {
      setError("That machine is an attached REPL session (attach-only) — it can't run new tasks. Pick a repo to use a cloud container instead.");
      return;
    }
    setBusy(true);
    try {
      const r = await fetch("/api/bridge/v1/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          environment_id: environmentId,
          prompt: [fileBlock, input.trim()].filter(Boolean).join("\n\n"),
          permission_mode: mode,
          model,
          images: imagesPayload,
          extra_repos: extraRepos,
          connectors,
        }),
      });
      if (r.ok) {
        const j = (await r.json()) as { session_id: string };
        setSessionId(j.session_id);
        setInput("");
        setAttachments([]);
        setExtraRepos([]);
        loadSessions();
      } else {
        const j = (await r.json().catch(() => ({}))) as { error?: string };
        setError(j.error ?? `Dispatch failed (${r.status})`);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  // Restore the saved sidebar width (post-mount, to avoid an SSR hydration
  // mismatch from reading localStorage during render).
  useEffect(() => {
    try {
      const saved = Number(localStorage.getItem("jarvis.code.sidebarWidth"));
      if (saved >= 200 && saved <= 480) setSidebarWidth(saved);
    } catch {
      /* no localStorage */
    }
  }, []);

  // URL prefill (claude.ai/code parity): ?prompt= / ?q= seeds the input and
  // ?repositories= / ?repo= preselects a repo, so an issue tracker can
  // deep-link into a ready-to-run task. Runs once on mount.
  useEffect(() => {
    try {
      const q = new URLSearchParams(window.location.search);
      const prompt = q.get("prompt") ?? q.get("q");
      if (prompt) setInput((cur) => cur || prompt);
      const repo = (q.get("repositories") ?? q.get("repo") ?? "").split(",")[0]?.trim();
      if (repo) setCloudRepo(repo);
    } catch {
      /* ignore */
    }
  }, []);

  // Keyboard shortcuts (claude.ai/code-style: single keys that fire only when
  // not typing; Esc always). Avoids browser-reserved modifier combos.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (shortcutsOpen) setShortcutsOpen(false);
        else if (sessionId && running) stopSession();
        return;
      }
      const el = e.target as HTMLElement | null;
      const typing =
        !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "n") {
        setSessionId(null);
        setShowRoutines(false);
        setInput("");
      } else if (e.key === "d" && sessionId) {
        setPanels((s) => ({ ...s, diff: !s.diff }));
      } else if (e.key === "?") {
        setShortcutsOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sessionId, running, shortcutsOpen]);

  // Drag the divider between the sidebar and the chat. Width clamps to
  // [200, 480]px and persists. clientX works because the sidebar starts at x=0.
  const startResize = (e: React.MouseEvent) => {
    e.preventDefault();
    let w = sidebarWidth;
    const onMove = (ev: MouseEvent) => {
      w = Math.min(480, Math.max(200, ev.clientX));
      setSidebarWidth(w);
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
      try {
        localStorage.setItem("jarvis.code.sidebarWidth", String(w));
      } catch {
        /* ignore */
      }
    };
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  // Archived sessions are read-only (the messages route 409s on archived), so
  // the composer is replaced by an "Unarchive" banner, like claude.ai/code.
  const currentArchived =
    !!sessionId && (sessions.find((s) => s.session_id === sessionId)?.archived ?? false);
  const unarchiveCurrent = async () => {
    if (!sessionId) return;
    try {
      await fetch(`/api/bridge/v1/sessions/${sessionId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ archived: false }),
      });
      loadSessions();
    } catch {
      /* ignore */
    }
  };

  // Open the environment-config modal. Resolves the env id from the selected
  // machine, else get-or-creates the cloud env for the picked repo (idempotent)
  // so config can be set even before the first dispatch.
  const openEnvConfig = async (envIdOverride?: string) => {
    setEnvCfgBusy(true);
    try {
      let envId = envIdOverride ?? selected?.environment_id ?? null;
      if (!envId && cloudRepo) {
        const r = await fetch("/api/bridge/v1/environments/cloud", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ repo: cloudRepo }),
        });
        if (r.ok) envId = ((await r.json()) as { environment_id: string }).environment_id;
      }
      if (!envId) {
        setError("Pick a repo or machine first to configure its environment.");
        return;
      }
      const cfg = await fetch(`/api/bridge/v1/environments/${envId}/config`);
      const j = cfg.ok
        ? ((await cfg.json()) as {
            envText: string;
            setupScript: string;
            networkLevel?: string;
            customAllowlist?: string;
          })
        : { envText: "", setupScript: "", networkLevel: "full", customAllowlist: "" };
      const name = machines?.find((m) => m.environment_id === envId)?.machine_name ?? "Default";
      setEnvCfg({
        id: envId,
        mode: "edit",
        name,
        envText: j.envText,
        setupScript: j.setupScript,
        networkLevel: j.networkLevel ?? "full",
        customAllowlist: j.customAllowlist ?? "",
      });
    } catch (e) {
      setError(String(e));
    } finally {
      setEnvCfgBusy(false);
    }
  };

  // "Add cloud environment…" — open the create modal (the env is created on save).
  const createCloudEnvironment = () => {
    setEnvCfg({ id: null, mode: "create", name: "", envText: "", setupScript: "", networkLevel: "trusted", customAllowlist: "" });
  };

  const saveEnvConfig = async () => {
    if (!envCfg) return;
    setEnvCfgBusy(true);
    try {
      let envId = envCfg.id;
      if (envCfg.mode === "create") {
        // A named, repo-less cloud environment; the repo is picked per session.
        const r = await fetch("/api/bridge/v1/environments/cloud", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: envCfg.name.trim() || "Default" }),
        });
        if (!r.ok) {
          setError("Could not create environment.");
          return;
        }
        envId = ((await r.json()) as { environment_id: string }).environment_id;
      }
      if (!envId) return;
      await fetch(`/api/bridge/v1/environments/${envId}/config`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: envCfg.name.trim() || undefined,
          envText: envCfg.envText,
          setupScript: envCfg.setupScript,
          networkLevel: envCfg.networkLevel,
          customAllowlist: envCfg.customAllowlist,
        }),
      });
      setEnvCfg(null);
      loadMachines();
    } catch (e) {
      setError(String(e));
    } finally {
      setEnvCfgBusy(false);
    }
  };

  const archiveEnvironment = async () => {
    if (!envCfg?.id) return;
    if (!window.confirm("Archive this environment? It won't be offered for new sessions.")) return;
    setEnvCfgBusy(true);
    try {
      await fetch(`/api/bridge/v1/environments/${envCfg.id}`, { method: "DELETE" });
      setEnvCfg(null);
      loadMachines();
    } catch (e) {
      setError(String(e));
    } finally {
      setEnvCfgBusy(false);
    }
  };

  return (
    // Full-screen overlay so /code presents like standalone Claude Code.
    <div className="fixed inset-0 z-40 flex bg-background text-foreground overflow-hidden">
      {codeSidebarOpen && (
        <>
          <CodeSidebar
            sessions={sessions}
            activeSessionId={sessionId}
            onSelectSession={(id) => { setSessionId(id || null); setShowRoutines(false); }}
            onNewSession={() => { setSessionId(null); setInput(""); setShowRoutines(false); }}
            onRefresh={loadSessions}
            onShareSession={(id) => { setSessionId(id); setShowRoutines(false); setShareOpen(true); }}
            routinesActive={showRoutines}
            onOpenRoutines={() => { setShowRoutines(true); setSessionId(null); }}
            width={sidebarWidth}
            onCollapse={() => setCodeSidebarOpen(false)}
          />

          {/* Draggable divider — a 1px hairline. Only the line tints on hover; the
              wider span is an invisible grab zone (no fill) so it stays easy to hit. */}
          <div
            onMouseDown={startResize}
            role="separator"
            aria-orientation="vertical"
            title="Drag to resize"
            className="relative w-px shrink-0 cursor-col-resize bg-border/50 transition-colors hover:bg-border active:bg-border"
          >
            <span className="absolute inset-y-0 -left-1.5 -right-1.5" />
          </div>
        </>
      )}

      {/* Collapsed → a thin rail with a button re-opens it (no overlap with the
          session header; mirrors a collapsed sidebar). */}
      {!codeSidebarOpen && (
        <div className="flex shrink-0 flex-col items-center border-r border-border/40 px-1.5 pt-2.5">
          <button
            type="button"
            onClick={() => setCodeSidebarOpen(true)}
            aria-label="Open sidebar"
            title="Open sidebar"
            className="flex size-7 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
          >
            <PanelLeftOpen className="size-4" />
          </button>
        </div>
      )}

      <main className="flex flex-1 overflow-hidden">
        {/* chat column (messages + composer) */}
        <div className="flex min-w-0 flex-1 flex-col">
          {showRoutines ? (
            <RoutinesView onOpenSession={(id) => { setSessionId(id); setShowRoutines(false); }} />
          ) : sessionId ? (
            <CodeSession
              sessionId={sessionId}
              repo={
                // The session's own repo (cloud env / machine) → the just-picked
                // repo → the selected machine. Show the short name (last segment).
                (sessions.find((s) => s.session_id === sessionId)?.repo ??
                  cloudRepo ??
                  repoLabel(selected))
                  ?.split("/")
                  .pop() ?? null
              }
              title={sessions.find((s) => s.session_id === sessionId)?.title ?? "New session"}
              // Full owner/name for the header menu's "Open on GitHub".
              repoFull={
                sessions.find((s) => s.session_id === sessionId)?.repo ??
                cloudRepo ??
                repoLabel(selected) ??
                null
              }
              panels={panels}
              onTogglePanel={(p) => setPanels((s) => ({ ...s, [p]: !s[p] }))}
              onShare={() => setShareOpen(true)}
              onRunningChange={setRunning}
              sendNonce={sendNonce}
              // Header-menu mutations: refresh the list always; archive/delete
              // also drop back to the welcome view (the session is gone/hidden).
              onMutated={(kind) => {
                loadSessions();
                if (kind !== "rename") setSessionId(null);
              }}
              // "Edit environment" → configure THIS session's env (resolved
              // from the session, not the composer's new-session pickers).
              onEditEnvironment={() => {
                const env = sessions.find((s) => s.session_id === sessionId)?.environment_id;
                void openEnvConfig(env ?? undefined);
              }}
            />
          ) : (
            <div className="flex-1 overflow-y-auto">
              <div className="mx-auto max-w-3xl px-8 pt-8">
                <div className="flex items-center gap-2.5 text-[24px] font-serif font-semibold text-foreground">
                  <Asterisk className="size-6 text-orange-500" strokeWidth={2.5} />
                  <span>Welcome back, Ulrich</span>
                </div>

                <div className="mt-8">
                  <div className="mb-2 text-[12px] font-medium text-muted-foreground">Sessions</div>
                  <div className="space-y-1">
                    {sessions.length === 0 ? (
                      <div className="rounded-lg bg-accent/20 px-3.5 py-3 text-[13px] text-muted-foreground">
                        No sessions yet — describe a task below to start one.
                      </div>
                    ) : (
                      sessions.map((s) => {
                        const m = STATUS_META[s.status];
                        return (
                          <button
                            key={s.session_id}
                            type="button"
                            onClick={() => setSessionId(s.session_id)}
                            className="group flex w-full items-center gap-2.5 rounded-lg bg-accent/20 px-3.5 py-2.5 text-left hover:bg-accent/40 transition-colors"
                          >
                            <span className={`size-1.5 shrink-0 rounded-full ${m.dot}`} />
                            <span className={`shrink-0 text-[12px] font-medium ${m.text}`}>{m.label}</span>
                            <span className="shrink-0 text-[13px] font-medium text-foreground">{s.title}</span>
                            <span className="min-w-0 flex-1 truncate text-[13px] text-muted-foreground/80">{s.preview}</span>
                            {s.repo && <span className="shrink-0 text-[12px] text-muted-foreground">{s.repo}</span>}
                            <span className="shrink-0 text-[12px] text-muted-foreground">{timeAgo(s.created_at)}</span>
                            <ChevronRight className="size-4 shrink-0 text-muted-foreground/60" />
                          </button>
                        );
                      })
                    )}
                  </div>
                </div>
              </div>
            </div>
          )}

          {!showRoutines && (
          <div className="mx-auto w-full max-w-3xl px-6 pb-6">
            {authExpired && (
              <div className="mb-2 flex items-center gap-2 text-[12px] text-amber-500">
                <span>Your session expired.</span>
                <button
                  type="button"
                  className="underline underline-offset-2 hover:opacity-80"
                  onClick={() => {
                    const next = encodeURIComponent(
                      window.location.pathname + window.location.search,
                    );
                    window.location.href = `/login?next=${next}`;
                  }}
                >
                  Sign in again
                </button>
              </div>
            )}
            {error && <div className="mb-2 text-[12px] text-red-500">{error}</div>}
            {!currentArchived && diffComments.length > 0 && (
              <div className="mb-2 flex flex-wrap gap-1.5">
                {diffComments.map((c, i) => (
                  <span
                    key={i}
                    className="inline-flex items-center gap-1 rounded-md bg-accent/40 px-2 py-1 text-[11px] text-foreground/80"
                  >
                    <span className="font-mono text-muted-foreground">
                      {c.file.split("/").pop()}:{c.line}
                    </span>
                    <span className="max-w-[160px] truncate">{c.text}</span>
                    <button
                      type="button"
                      onClick={() => setDiffComments((arr) => arr.filter((_, j) => j !== i))}
                      className="text-muted-foreground hover:text-foreground"
                      aria-label="Remove comment"
                    >
                      <X className="size-3" />
                    </button>
                  </span>
                ))}
              </div>
            )}
            {currentArchived ? (
              <div className="flex items-center justify-between gap-3 rounded-xl border border-border/60 bg-accent/20 px-4 py-3">
                <span className="text-[13px] text-muted-foreground">
                  This session is archived. Unarchive it to continue the conversation.
                </span>
                <button
                  type="button"
                  onClick={unarchiveCurrent}
                  className="shrink-0 rounded-md bg-secondary px-3 py-1.5 text-[12.5px] font-medium text-secondary-foreground hover:bg-secondary/80"
                >
                  Unarchive
                </button>
              </div>
            ) : (
            <CodeComposer
              value={input}
              onChange={setInput}
              onSubmit={dispatch}
              busy={busy}
              machines={machines}
              selected={selected}
              onPickMachine={setSelected}
              onRefreshMachines={loadMachines}
              onConfigureEnvironment={(id) => openEnvConfig(id)}
              onAddCloudEnvironment={createCloudEnvironment}
              placeholder={sessionId ? "Type / for commands" : "Describe a task or ask a question"}
              showPills={!sessionId}
              mode={mode}
              onModeChange={changeMode}
              model={model}
              onModelChange={changeModel}
              connectors={connectors}
              onConnectorsChange={setConnectors}
              availableConnectors={availableConnectors}
              connectorsEditable={!sessionId}
              onPickRepo={setCloudRepo}
              extraRepos={extraRepos}
              onExtraReposChange={setExtraRepos}
              attachments={attachments}
              onAttachmentsChange={setAttachments}
              running={sessionId ? running : false}
              onStop={stopSession}
              onCommand={(name) => {
                if (name === "clear") {
                  setSessionId(null);
                  setInput("");
                } else if (name === "diff") {
                  setPanels((s) => ({ ...s, diff: true }));
                } else if (name === "help") {
                  setShortcutsOpen(true);
                }
              }}
            />
            )}
          </div>
          )}
        </div>

        {/* right-side panels (session mode) */}
        {sessionId && (panels.diff || panels.background || panels.plan) && (
          <CodePanels
            panels={panels}
            onClose={(p: PanelName) => setPanels((s) => ({ ...s, [p]: false }))}
            baseBranch={selected?.branch ?? "main"}
            workBranch={`jarvis/${sessionId.slice(0, 8)}`}
            sessionId={sessionId}
            onComment={(file, line, text) =>
              setDiffComments((c) => [...c, { file, line, text }])
            }
          />
        )}
      </main>

      {/* Keyboard shortcuts overlay (?) */}
      {shortcutsOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={() => setShortcutsOpen(false)}
        >
          <div
            className="w-[360px] max-w-[90vw] rounded-2xl border border-border bg-card p-5 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-3 flex items-center justify-between">
              <div className="text-[15px] font-semibold text-foreground">Keyboard shortcuts</div>
              <button
                type="button"
                onClick={() => setShortcutsOpen(false)}
                aria-label="Close"
                className="text-muted-foreground hover:text-foreground"
              >
                <X className="size-4" />
              </button>
            </div>
            <div className="space-y-2 text-[13px]">
              {[
                ["n", "New session"],
                ["d", "Toggle diff panel"],
                ["Esc", "Stop the running task"],
                ["?", "Show this help"],
              ].map(([k, label]) => (
                <div key={k} className="flex items-center justify-between">
                  <span className="text-foreground/80">{label}</span>
                  <kbd className="rounded border border-border bg-accent/40 px-1.5 py-0.5 font-mono text-[11px] text-foreground/70">
                    {k}
                  </kbd>
                </div>
              ))}
            </div>
            <p className="mt-3 text-[11px] text-muted-foreground/60">
              Shortcuts fire when you&apos;re not typing in a field.
            </p>
          </div>
        </div>
      )}

      {/* Environment config modal (env vars + setup script) */}
      {envCfg && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={() => setEnvCfg(null)}
        >
          <div
            className="w-[560px] max-w-[92vw] rounded-2xl border border-border bg-card p-5 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-1 flex items-center justify-between">
              <div className="text-[15px] font-semibold text-foreground">
                {envCfg.mode === "create" ? "New cloud environment" : "Update cloud environment"}
              </div>
              <button
                type="button"
                onClick={() => setEnvCfg(null)}
                aria-label="Close"
                className="text-muted-foreground hover:text-foreground"
              >
                <X className="size-4" />
              </button>
            </div>
            <div className="mb-3 text-[12.5px] text-muted-foreground">
              Changes to your environment will apply to new sessions.
            </div>

            <label className="mb-1 block text-[12px] font-medium text-foreground/80">Name</label>
            <input
              value={envCfg.name}
              onChange={(e) => setEnvCfg((c) => (c ? { ...c, name: e.target.value } : c))}
              placeholder="Default"
              className="mb-3 w-full rounded-lg border border-border bg-background px-3 py-2 text-[12.5px] text-foreground outline-none focus:border-orange-500/60"
            />

            <label className="mb-1 block text-[12px] font-medium text-foreground/80">Network access</label>
            <select
              value={envCfg.networkLevel}
              onChange={(e) => setEnvCfg((c) => (c ? { ...c, networkLevel: e.target.value } : c))}
              className="mb-2 w-full rounded-lg border border-border bg-background px-3 py-2 text-[12.5px] text-foreground outline-none focus:border-orange-500/60"
            >
              <option value="full">Full — unrestricted (default)</option>
              <option value="trusted">Trusted — package registries + GitHub only</option>
              <option value="custom">Custom — trusted + your domains</option>
              <option value="none">None — no internet (callback only)</option>
            </select>
            {envCfg.networkLevel === "custom" && (
              <textarea
                value={envCfg.customAllowlist}
                onChange={(e) => setEnvCfg((c) => (c ? { ...c, customAllowlist: e.target.value } : c))}
                rows={2}
                spellCheck={false}
                placeholder={"api.example.com\n.internal.corp"}
                className="mb-2 w-full resize-y rounded-lg border border-border bg-background px-3 py-2 font-mono text-[12px] text-foreground outline-none focus:border-orange-500/60"
              />
            )}
            <p className="mb-3 text-[11px] text-muted-foreground/60">
              Non-Full levels route egress through an allowlist proxy (applies to new sessions).
            </p>

            <label className="mb-1 block text-[12px] font-medium text-foreground/80">Environment variables</label>
            <p className="mb-1 text-[11px] text-muted-foreground/60">
              In .env format. Visible to anyone using this environment — don&apos;t add secrets.
            </p>
            <textarea
              value={envCfg.envText}
              onChange={(e) => setEnvCfg((c) => (c ? { ...c, envText: e.target.value } : c))}
              rows={5}
              spellCheck={false}
              placeholder={"NODE_ENV=production\nGIT_AUTHOR_NAME=Your Name"}
              className="mb-3 w-full resize-y rounded-lg border border-border bg-background px-3 py-2 font-mono text-[12px] text-foreground outline-none focus:border-orange-500/60"
            />

            <label className="mb-1 block text-[12px] font-medium text-foreground/80">Setup script</label>
            <p className="mb-1 text-[11px] text-muted-foreground/60">
              Bash script that runs when a new session starts, before Jarvis Code launches.
            </p>
            <textarea
              value={envCfg.setupScript}
              onChange={(e) => setEnvCfg((c) => (c ? { ...c, setupScript: e.target.value } : c))}
              rows={5}
              spellCheck={false}
              placeholder={"#!/bin/bash\nnpm install"}
              className="mb-4 w-full resize-y rounded-lg border border-border bg-background px-3 py-2 font-mono text-[12px] text-foreground outline-none focus:border-orange-500/60"
            />

            <div className="flex items-center justify-between gap-2">
              <div>
                {envCfg.mode === "edit" && (
                  <button
                    type="button"
                    onClick={archiveEnvironment}
                    disabled={envCfgBusy}
                    className="rounded-md px-3 py-1.5 text-[13px] text-red-500 hover:bg-red-500/10 disabled:opacity-60"
                  >
                    Archive
                  </button>
                )}
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setEnvCfg(null)}
                  className="rounded-md px-3 py-1.5 text-[13px] text-muted-foreground hover:text-foreground"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={saveEnvConfig}
                  disabled={envCfgBusy}
                  className="rounded-md bg-orange-600 px-3 py-1.5 text-[13px] font-medium text-white hover:bg-orange-500 disabled:opacity-60"
                >
                  {envCfgBusy ? "Saving…" : envCfg.mode === "create" ? "Create environment" : "Save changes"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Share session modal */}
      {shareOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setShareOpen(false)}>
          <div className="w-[420px] max-w-[90vw] rounded-2xl border border-border bg-card p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="mb-1 flex items-center justify-between">
              <div className="text-[15px] font-semibold text-foreground">Share session</div>
              <button type="button" onClick={() => setShareOpen(false)} aria-label="Close" className="text-muted-foreground hover:text-foreground">
                <X className="size-4" />
              </button>
            </div>
            <div className="mb-4 text-[12.5px] text-muted-foreground">Showcase your work and how you code with Jarvis.</div>
            {([
              { key: "private", icon: Shield, title: "Private", sub: "Only you have access" },
              { key: "public", icon: ExternalLink, title: "Public", sub: "Anyone with the link can view" },
            ] as const).map((o) => (
              <button
                key={o.key}
                type="button"
                onClick={() => setShareVisibility(o.key)}
                className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left hover:bg-accent/40"
              >
                <o.icon className="size-4 text-muted-foreground" />
                <div className="flex-1">
                  <div className="text-[13px] font-medium text-foreground">{o.title}</div>
                  <div className="text-[12px] text-muted-foreground">{o.sub}</div>
                </div>
                {shareVisibility === o.key && <Check className="size-4 text-foreground" />}
              </button>
            ))}
            <div className="mt-3 text-[11.5px] leading-relaxed text-muted-foreground/70">
              Don&apos;t share personal information or third-party content without permission, and see our{" "}
              <span className="text-blue-400">Usage Policy</span>.
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
