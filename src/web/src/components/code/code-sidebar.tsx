"use client";

import Link from "next/link";
import { useState, useEffect } from "react";
import { UserMenu } from "@/components/layout/user-menu";
import {
  ChevronDown,
  ChevronRight,
  Briefcase,
  Plus,
  Zap,
  Search,
  PanelLeft,
  ArrowDownUp,
  Send,
  MoreVertical,
  Pencil,
  Pin,
  PinOff,
  Link2,
  ExternalLink,
  MailOpen,
  Share2,
  FolderInput,
  FolderPlus,
  Archive,
  Trash2,
  Check,
} from "lucide-react";

type SessionSummary = {
  session_id: string;
  title: string;
  status: "needs_input" | "working" | "done";
  created_at?: number;
  pinned?: boolean;
  read?: boolean;
  archived?: boolean;
  preview?: string;
  repo?: string | null;
  group_id?: string | null;
  group_name?: string | null;
};

type Group = { group_id: string; name: string };

const NAV_BTN =
  "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13px] text-sidebar-foreground/85 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground transition-colors w-full text-left";

/** "2m ago" / "3h ago" — relative time for the chat list. */
function timeAgo(ts?: number): string {
  if (!ts) return "";
  const s = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d ago`;
  return `${Math.floor(d / 7)}w ago`;
}

const DOT: Record<SessionSummary["status"], string> = {
  needs_input: "bg-amber-500",
  working: "bg-blue-500",
  done: "border border-sidebar-foreground/40",
};

export function CodeSidebar({
  onNewSession,
  sessions = [],
  activeSessionId,
  onSelectSession,
  onRefresh,
  onShareSession,
  routinesActive = false,
  onOpenRoutines,
  width = 260,
  onCollapse,
}: {
  onNewSession: () => void;
  sessions?: SessionSummary[];
  activeSessionId?: string | null;
  onSelectSession?: (id: string) => void;
  onRefresh?: () => void;
  onShareSession?: (id: string) => void;
  routinesActive?: boolean;
  onOpenRoutines?: () => void;
  /** Sidebar width in px (drag-resizable from the page). */
  width?: number;
  /** Collapse the sidebar (hide it; a floating button on the page reopens it). */
  onCollapse?: () => void;
}) {
  // The menu is positioned fixed (computed from the kebab's rect) so it
  // escapes the Recents list's overflow-y-auto clip — left-full inside that
  // scroll container was cut off at the sidebar's right edge.
  const [menu, setMenu] = useState<{ id: string; top: number; left: number } | null>(null);
  const [sub, setSub] = useState<null | "openin" | "group">(null);
  const [groups, setGroups] = useState<Group[]>([]);
  // Sidebar filter (Active/Archived/All) + text search, like claude.ai/code.
  const [filter, setFilter] = useState<"active" | "archived" | "all">("active");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    window.addEventListener("mousedown", close);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [menu]);

  // Load groups once a menu opens (for the "Move to group" submenu).
  useEffect(() => {
    if (!menu) {
      setSub(null);
      return;
    }
    fetch("/api/bridge/v1/groups")
      .then((r) => (r.ok ? r.json() : null))
      .then((j: { groups?: Group[] } | null) => j?.groups && setGroups(j.groups))
      .catch(() => {});
  }, [menu]);

  const openMenu = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (menu?.id === id) {
      setMenu(null);
      return;
    }
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const MENU_H = 360; // full menu; clamp so the bottom isn't cut off
    const top = Math.min(r.top, Math.max(8, window.innerHeight - MENU_H - 8));
    setMenu({ id, top, left: r.right + 6 });
  };

  // deselect: drop the active session after the mutation (archive/delete make
  // it un-viewable; rename/pin keep it open).
  const mutate = async (init: RequestInit, id: string, deselect = false) => {
    setBusy(true);
    try {
      await fetch(`/api/bridge/v1/sessions/${id}`, init);
    } catch {
      /* surfaced by the refresh showing no change */
    } finally {
      setBusy(false);
      setMenu(null);
      if (deselect && activeSessionId === id) onSelectSession?.("");
      onRefresh?.();
    }
  };

  const patch = (id: string, body: Record<string, unknown>, deselect = false) =>
    void mutate(
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
      id,
      deselect,
    );

  const rename = (id: string, current: string) => {
    const next = window.prompt("Rename session", current);
    if (next === null || !next.trim() || next.trim() === current) {
      setMenu(null);
      return;
    }
    patch(id, { title: next.trim() });
  };

  const togglePin = (id: string, pinned: boolean) => patch(id, { pinned: !pinned });

  const archive = (id: string) => patch(id, { archived: true }, true);

  const copyLink = (id: string) => {
    const url = `${window.location.origin}/code/session_${id}`;
    void navigator.clipboard?.writeText(url).catch(() => {});
    setMenu(null);
  };

  const markRead = (id: string, read: boolean) => patch(id, { read: !read });

  const moveToGroup = (id: string, groupId: string | null) =>
    patch(id, { group_id: groupId });

  const newGroup = async (id: string) => {
    const name = window.prompt("New group name");
    if (!name || !name.trim()) {
      setMenu(null);
      return;
    }
    setBusy(true);
    try {
      const r = await fetch("/api/bridge/v1/groups", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim() }),
      });
      if (r.ok) {
        const { group_id } = (await r.json()) as { group_id: string };
        await fetch(`/api/bridge/v1/sessions/${id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ group_id }),
        });
      }
    } catch {
      /* refresh will show no change on failure */
    } finally {
      setBusy(false);
      setMenu(null);
      onRefresh?.();
    }
  };

  const openOnGitHub = (repo?: string | null) => {
    setMenu(null);
    if (repo && repo.includes("/")) {
      window.open(`https://github.com/${repo}`, "_blank", "noopener");
    }
  };

  const copyId = (id: string) => {
    void navigator.clipboard?.writeText(id).catch(() => {});
    setMenu(null);
  };

  const share = (id: string) => {
    setMenu(null);
    onShareSession?.(id);
  };

  const remove = (id: string) => {
    if (!window.confirm("Delete this session permanently? This cannot be undone.")) {
      setMenu(null);
      return;
    }
    void mutate({ method: "DELETE" }, id, true);
  };

  const menuSession = menu ? sessions.find((s) => s.session_id === menu.id) : null;

  return (
    <aside
      style={{ width }}
      className="shrink-0 h-full flex flex-col bg-sidebar text-sidebar-foreground border-r border-border/40"
    >
      {/* Branding header */}
      <div className="flex items-center justify-between px-3 pt-3 pb-2">
        <div className="flex items-center gap-2">
          <Link href="/chat" className="whitespace-nowrap font-serif text-[14px] font-bold leading-none text-sidebar-foreground">
            Jarvis&nbsp;Code
          </Link>
          <span className="whitespace-nowrap rounded border border-border/60 px-1.5 py-0.5 text-[9.5px] leading-none text-sidebar-foreground/55">
            Research preview
          </span>
        </div>
        <div className="flex items-center gap-0.5">
          <button type="button" aria-label="Collapse sidebar" title="Collapse sidebar" onClick={onCollapse} className="flex size-6 items-center justify-center rounded text-sidebar-foreground/50 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground">
            <PanelLeft className="size-4" />
          </button>
          <button type="button" aria-label="Search" className="flex size-6 items-center justify-center rounded text-sidebar-foreground/50 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground">
            <Search className="size-4" />
          </button>
        </div>
      </div>

      {/* Nav */}
      <div className="px-2 pt-1">
        <button type="button" onClick={onNewSession} className={`${NAV_BTN} bg-sidebar-accent/60 text-sidebar-foreground`}>
          <Plus className="size-4 shrink-0 text-sidebar-foreground/70" />
          New session
        </button>
        <button
          type="button"
          onClick={onOpenRoutines}
          className={`${NAV_BTN} ${routinesActive ? "bg-sidebar-accent/60 text-sidebar-foreground" : ""}`}
        >
          <Zap className="size-4 shrink-0 text-sidebar-foreground/60" />
          Routines
        </button>
        <button type="button" className={NAV_BTN}>
          <Send className="size-4 shrink-0 text-sidebar-foreground/60" />
          <span>Dispatch</span>
          <span className="ml-1 rounded border border-border/60 px-1 py-0.5 text-[9px] leading-none text-sidebar-foreground/50">
            Beta
          </span>
        </button>
        <button type="button" className={NAV_BTN}>
          <Briefcase className="size-4 shrink-0 text-sidebar-foreground/60" />
          Customize
        </button>
        <button type="button" className={NAV_BTN}>
          <ChevronDown className="size-4 shrink-0 text-sidebar-foreground/60" />
          More
        </button>
      </div>

      {/* Recents (sessions with status dots) */}
      <div className="mt-4 flex-1 overflow-y-auto px-2">
        <div className="mb-1 flex items-center justify-between px-2.5">
          <span className="text-[11px] font-medium text-sidebar-foreground/45">Recents</span>
          <button type="button" aria-label="Sort" className="flex size-5 items-center justify-center rounded text-sidebar-foreground/40 hover:text-sidebar-foreground">
            <ArrowDownUp className="size-3" />
          </button>
        </div>
        <div className="mb-1.5 px-2">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search sessions…"
            className="mb-1.5 w-full rounded-md bg-sidebar-accent/40 px-2 py-1 text-[12px] text-sidebar-foreground placeholder:text-sidebar-foreground/35 outline-none focus:bg-sidebar-accent/70"
          />
          <div className="flex items-center rounded-md bg-sidebar-accent/30 p-0.5 text-[10.5px]">
            {(["active", "archived", "all"] as const).map((f) => (
              <button
                key={f}
                type="button"
                onClick={() => setFilter(f)}
                className={`flex-1 rounded px-1.5 py-0.5 capitalize ${filter === f ? "bg-sidebar text-sidebar-foreground shadow-sm" : "text-sidebar-foreground/50 hover:text-sidebar-foreground"}`}
              >
                {f}
              </button>
            ))}
          </div>
        </div>
        {(() => {
          const ql = q.trim().toLowerCase();
          const shown = sessions.filter(
            (s) =>
              (filter === "all" ? true : filter === "archived" ? s.archived : !s.archived) &&
              (!ql || `${s.title} ${s.preview ?? ""}`.toLowerCase().includes(ql)),
          );
          return (
        <div className="space-y-px">
          {shown.length === 0 ? (
            <div className="px-2.5 py-1 text-[12.5px] text-sidebar-foreground/35">{sessions.length === 0 ? "No sessions yet" : "No matching sessions"}</div>
          ) : (
            shown.map((s) => (
              <div
                key={s.session_id}
                className={`group relative flex items-center rounded-md ${
                  activeSessionId === s.session_id ? "bg-sidebar-accent/60" : "hover:bg-sidebar-accent/50"
                }`}
              >
                <button
                  type="button"
                  onClick={() => onSelectSession?.(s.session_id)}
                  className="flex min-w-0 flex-1 items-center gap-2.5 px-2.5 py-1.5 text-left text-[12.5px] text-sidebar-foreground/75 group-hover:text-sidebar-foreground"
                >
                  {/* read clears the colored status dot to a neutral one */}
                  <span className={`size-1.5 shrink-0 rounded-full ${s.read ? "border border-sidebar-foreground/30" : DOT[s.status]}`} />
                  {s.pinned && <Pin className="size-3 shrink-0 -rotate-45 text-sidebar-foreground/50" />}
                  <span className="truncate">{s.title}</span>
                  {s.group_name && (
                    <span className="ml-1 shrink-0 rounded bg-sidebar-accent/70 px-1 py-0.5 text-[9.5px] leading-none text-sidebar-foreground/55">
                      {s.group_name}
                    </span>
                  )}
                </button>
                {/* time → hidden on hover so the kebab can take its place */}
                <span className="mr-1 shrink-0 text-[11px] text-sidebar-foreground/40 group-hover:hidden">
                  {timeAgo(s.created_at)}
                </span>
                <button
                  type="button"
                  aria-label="Session options"
                  onClick={(e) => openMenu(s.session_id, e)}
                  className={`mr-1 hidden size-5 shrink-0 items-center justify-center rounded text-sidebar-foreground/50 hover:bg-sidebar-accent hover:text-sidebar-foreground group-hover:flex ${
                    menu?.id === s.session_id ? "!flex bg-sidebar-accent" : ""
                  }`}
                >
                  <MoreVertical className="size-3.5" />
                </button>
              </div>
            ))
          )}
        </div>
          );
        })()}
      </div>

      {/* User footer — clickable menu (email · Settings · Get help · Log out).
          No "· Max" plan tier — not applicable to self-hosted JARVIS. */}
      <footer aria-label="User" className="border-t border-border/40 px-2 py-1.5">
        <UserMenu fallbackName="Ulrich" />
      </footer>

      {/* Session menu — fixed so it isn't clipped by the Recents scroll box.
          Full claude.ai option set, order + shortcuts matched. "Open in" and
          "Move to group" expand inline (robust in a fixed-position menu). */}
      {menu && menuSession && (
        <div
          className="fixed z-[70] w-[212px] rounded-lg border border-border bg-card p-1 shadow-xl"
          style={{ top: menu.top, left: menu.left }}
          onMouseDown={(e) => e.stopPropagation()}
          onKeyDown={(e) => {
            const k = e.key.toLowerCase();
            if (k === "p") togglePin(menuSession.session_id, !!menuSession.pinned);
            else if (k === "u") markRead(menuSession.session_id, !!menuSession.read);
            else if (k === "r") rename(menuSession.session_id, menuSession.title);
            else if (k === "c") copyLink(menuSession.session_id);
            else if (k === "a") archive(menuSession.session_id);
            else if (k === "d") remove(menuSession.session_id);
            else if (e.key === "Escape") setMenu(null);
          }}
          tabIndex={-1}
          ref={(el) => el?.focus()}
        >
          <MenuItem icon={ExternalLink} label="Open in" expand expanded={sub === "openin"} onClick={() => setSub((s) => (s === "openin" ? null : "openin"))} />
          {sub === "openin" && (
            <div className="ml-2 border-l border-border/40 pl-1">
              <MenuItem icon={Briefcase} label="Open on GitHub" disabled={!menuSession.repo} onClick={() => openOnGitHub(menuSession.repo)} />
              <MenuItem icon={Link2} label="Copy session ID" onClick={() => copyId(menuSession.session_id)} />
            </div>
          )}
          <MenuItem icon={menuSession.pinned ? PinOff : Pin} label={menuSession.pinned ? "Unpin" : "Pin"} chord="P" disabled={busy} onClick={() => togglePin(menuSession.session_id, !!menuSession.pinned)} />
          <MenuItem icon={MailOpen} label={menuSession.read ? "Mark as unread" : "Mark as read"} chord="U" disabled={busy} onClick={() => markRead(menuSession.session_id, !!menuSession.read)} />
          <MenuItem icon={Pencil} label="Rename" chord="R" disabled={busy} onClick={() => rename(menuSession.session_id, menuSession.title)} />
          <MenuItem icon={Share2} label="Share" disabled={busy} onClick={() => share(menuSession.session_id)} />
          <MenuItem icon={Link2} label="Copy link" chord="C" disabled={busy} onClick={() => copyLink(menuSession.session_id)} />
          <MenuItem icon={FolderInput} label="Move to group" expand expanded={sub === "group"} onClick={() => setSub((s) => (s === "group" ? null : "group"))} />
          {sub === "group" && (
            <div className="ml-2 max-h-[160px] overflow-y-auto border-l border-border/40 pl-1">
              {menuSession.group_id && (
                <MenuItem icon={FolderInput} label="Remove from group" onClick={() => moveToGroup(menuSession.session_id, null)} />
              )}
              {groups.map((g) => (
                <MenuItem
                  key={g.group_id}
                  icon={FolderInput}
                  label={g.name}
                  check={menuSession.group_id === g.group_id}
                  disabled={busy}
                  onClick={() => moveToGroup(menuSession.session_id, g.group_id)}
                />
              ))}
              <MenuItem icon={FolderPlus} label="New group…" disabled={busy} onClick={() => newGroup(menuSession.session_id)} />
            </div>
          )}
          <div className="my-1 border-t border-border/50" />
          <MenuItem icon={Archive} label="Archive" chord="A" disabled={busy} onClick={() => archive(menuSession.session_id)} />
          <MenuItem icon={Trash2} label="Delete" chord="D" danger disabled={busy} onClick={() => remove(menuSession.session_id)} />
        </div>
      )}
    </aside>
  );
}

function MenuItem({
  icon: Icon,
  label,
  chord,
  danger,
  disabled,
  expand,
  expanded,
  check,
  onClick,
}: {
  icon: typeof Pin;
  label: string;
  chord?: string;
  danger?: boolean;
  disabled?: boolean;
  expand?: boolean;
  expanded?: boolean;
  check?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={`flex w-full items-center gap-2.5 rounded px-2.5 py-1.5 text-left text-[13px] disabled:opacity-50 ${
        danger ? "text-red-500 hover:bg-red-500/10" : "text-foreground/90 hover:bg-accent/50"
      }`}
    >
      <Icon className={`size-3.5 shrink-0 ${danger ? "" : "text-muted-foreground"}`} />
      <span className="flex-1 truncate">{label}</span>
      {check && <Check className="size-3.5 text-primary" />}
      {expand ? (
        expanded ? (
          <ChevronDown className="size-3.5 text-muted-foreground/60" />
        ) : (
          <ChevronRight className="size-3.5 text-muted-foreground/60" />
        )
      ) : chord ? (
        <span className={`text-[11px] ${danger ? "text-red-500/60" : "text-muted-foreground/60"}`}>{chord}</span>
      ) : null}
    </button>
  );
}
