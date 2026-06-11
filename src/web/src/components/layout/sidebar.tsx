"use client";

import { motion, AnimatePresence } from "motion/react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  Code2,
  Hammer,
  MessagesSquare,
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Search,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { UserMenu } from "./user-menu";
import { useUI } from "@/stores/ui";
import { useChatStore } from "@/stores/chat";
import {
  useConversations,
  useRenameConversation,
  type ConversationSummary,
} from "@/hooks/use-conversations";
import { useSettings } from "@/hooks/use-settings";
import { cn } from "@/lib/utils";
import { DEFAULT_MODEL, MODELS_META } from "@/lib/ai/models-meta";
import { PROVIDER_FEATURES, PROVIDER_SECTIONS } from "@/lib/ai/features";
import { getProviderUX } from "@/lib/ai/provider-ux";

const CORE_NAV = [
  { href: "/chat", label: "New chat", icon: Plus },
  { href: "/search", label: "Search", icon: Search },
  { href: "/chats", label: "Chats", icon: MessagesSquare },
  { href: "/code", label: "Code", icon: Code2 },
  { href: "/workbench", label: "Workbench", icon: Hammer },
] as const;

// Bucket a conversation into Today / Yesterday / Last 7 days / Older
// based on its updatedAt timestamp. Bucket label drives the section
// header in the sidebar Recents list. Older entries collapse into a
// single "Older" group so a long history doesn't sprawl.
type Bucket = "today" | "yesterday" | "week" | "older";
const BUCKET_LABEL: Record<Bucket, string> = {
  today: "Today",
  yesterday: "Yesterday",
  week: "Last 7 days",
  older: "Older",
};
function bucketOf(updatedAt: string): Bucket {
  const t = new Date(updatedAt).getTime();
  const now = new Date();
  const startOfToday = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate(),
  ).getTime();
  if (t >= startOfToday) return "today";
  if (t >= startOfToday - 24 * 60 * 60 * 1000) return "yesterday";
  if (t >= startOfToday - 7 * 24 * 60 * 60 * 1000) return "week";
  return "older";
}

function initials(name?: string | null) {
  if (!name) return "YO";
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]!.toUpperCase())
    .join("");
}

export function Sidebar() {
  const { sidebarOpen, toggleSidebar } = useUI();
  const pathname = usePathname();
  const { data: conversations, isLoading } = useConversations();
  const { data: settings } = useSettings();
  const [moreOpen, setMoreOpen] = useState(false);

  const modelId = useChatStore((s) => s.model);
  const activeModel = MODELS_META[modelId] ?? MODELS_META[DEFAULT_MODEL];
  const provider = activeModel.provider;
  // Sidebar layout is locked to Anthropic — model switches only change the
  // backend, not which nav sections appear.
  const features = PROVIDER_FEATURES["anthropic"];
  const primary = features.filter((f) => !f.overflow);
  const overflow = features.filter((f) => f.overflow);
  const sections = PROVIDER_SECTIONS["anthropic"] ?? [];
  const ux = getProviderUX("anthropic");
  const recentsLabel = ux.recentsLabel ?? "Recents";

  const displayName = settings?.user?.name ?? "You";

  return (
    <>
      <AnimatePresence initial={false}>
        {sidebarOpen && (
          <motion.aside
            initial={{ width: 0, opacity: 0 }}
            animate={{ width: "16rem", opacity: 1 }}
            exit={{ width: 0, opacity: 0 }}
            transition={{ duration: 0.18, ease: "easeOut" }}
            className="shrink-0 overflow-hidden border-r border-border/60 bg-sidebar text-sidebar-foreground"
          >
            <div className="flex h-full w-64 flex-col">
              {/* Brand */}
              <div className="flex items-center justify-between px-4 py-3">
                <Link
                  href="/chat"
                  className="font-serif text-[18px] font-semibold tracking-tight text-sidebar-foreground"
                >
                  Jarvis
                </Link>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={toggleSidebar}
                  aria-label="Close sidebar"
                  className="size-7"
                >
                  <PanelLeftClose className="size-3.5" />
                </Button>
              </div>

              <div className="px-2">
                {/* Core nav */}
                <nav className="space-y-px">
                  {CORE_NAV.map((item) => {
                    const active =
                      item.href === "/chat"
                        ? pathname === "/chat"
                        : pathname.startsWith(item.href);
                    return (
                      <Link
                        key={item.href}
                        href={item.href}
                        className={cn(
                          "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13.5px] transition-colors",
                          active
                            ? "bg-sidebar-accent text-sidebar-accent-foreground"
                            : "text-sidebar-foreground/90 hover:bg-sidebar-accent/60",
                        )}
                      >
                        <item.icon className="size-4 shrink-0 text-sidebar-foreground/70" />
                        {item.label}
                      </Link>
                    );
                  })}
                </nav>

                {/* Provider features */}
                <nav className="mt-1 space-y-px">
                    {primary.map((f) => {
                      const href = f.href ?? `/anthropic/${f.slug}`;
                      const active = f.href
                        ? pathname.startsWith(f.href)
                        : pathname === href;
                      return (
                        <Link
                          key={f.slug}
                          href={href}
                          className={cn(
                            "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13.5px] transition-colors",
                            active
                              ? "bg-sidebar-accent text-sidebar-accent-foreground"
                              : "text-sidebar-foreground/90 hover:bg-sidebar-accent/60",
                          )}
                        >
                          <f.icon className="size-4 shrink-0 text-sidebar-foreground/70" />
                          <span className="flex-1 truncate">{f.label}</span>
                          {f.badge && (
                            <span className="rounded-sm bg-primary/15 px-1.5 py-px text-[9px] font-medium uppercase tracking-wide text-primary">
                              {f.badge}
                            </span>
                          )}
                        </Link>
                      );
                    })}
                </nav>

                {/* More */}
                {overflow.length > 0 && (
                  <div className="mt-1">
                    <button
                      type="button"
                      onClick={() => setMoreOpen((v) => !v)}
                      className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13.5px] text-sidebar-foreground/75 transition-colors hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground"
                    >
                      <ChevronDown
                        className={cn(
                          "size-3.5 shrink-0 text-sidebar-foreground/60 transition-transform",
                          !moreOpen && "-rotate-90",
                        )}
                      />
                      More
                    </button>
                    <AnimatePresence initial={false}>
                      {moreOpen && (
                        <motion.nav
                          initial={{ height: 0, opacity: 0 }}
                          animate={{ height: "auto", opacity: 1 }}
                          exit={{ height: 0, opacity: 0 }}
                          transition={{ duration: 0.15, ease: "easeOut" }}
                          className="overflow-hidden"
                        >
                          {overflow.map((f) => {
                            const href = f.href ?? `/anthropic/${f.slug}`;
                            const active = f.href
                              ? pathname.startsWith(f.href)
                              : pathname === href;
                            return (
                              <Link
                                key={f.slug}
                                href={href}
                                className={cn(
                                  "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 pl-8 text-[13.5px] transition-colors",
                                  active
                                    ? "bg-sidebar-accent text-sidebar-accent-foreground"
                                    : "text-sidebar-foreground/85 hover:bg-sidebar-accent/60",
                                )}
                              >
                                <f.icon className="size-4 shrink-0 text-sidebar-foreground/70" />
                                {f.label}
                              </Link>
                            );
                          })}
                        </motion.nav>
                      )}
                    </AnimatePresence>
                  </div>
                )}
              </div>

              {/* Provider sections (GPTs / Projects / etc) */}
              {sections.length > 0 && (
                <div className="mt-3 px-2 space-y-4">
                  {sections.map((s) => (
                    <div key={s.label}>
                      <div className="px-2.5 pb-1 text-[11px] text-sidebar-foreground/50">
                        {s.label}
                      </div>
                      <div className="space-y-px">
                        {s.items.map((item) => {
                          const active = pathname === item.href;
                          return (
                            <Link
                              key={item.label}
                              href={item.href}
                              className={cn(
                                "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13.5px] transition-colors",
                                active
                                  ? "bg-sidebar-accent text-sidebar-accent-foreground"
                                  : "text-sidebar-foreground/90 hover:bg-sidebar-accent/60",
                              )}
                            >
                              <span
                                className={cn(
                                  "flex size-5 shrink-0 items-center justify-center rounded-md",
                                  item.hueClass ?? "bg-sidebar-accent/60",
                                )}
                              >
                                <item.icon className="size-3 text-sidebar-foreground/90" />
                              </span>
                              <span className="truncate">{item.label}</span>
                            </Link>
                          );
                        })}
                        {s.footer && (
                          <Link
                            href={s.footer.href}
                            className="flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13.5px] text-sidebar-foreground/75 transition-colors hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground"
                          >
                            <s.footer.icon className="size-4 shrink-0 text-sidebar-foreground/60" />
                            {s.footer.label}
                          </Link>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* Recents — grouped + searchable + rename-able */}
              <RecentsList
                conversations={conversations}
                isLoading={isLoading}
                pathname={pathname}
                label={recentsLabel}
              />

              {/* User footer — avatar opens the account menu (Settings / Log out) */}
              <div className="border-t border-border/50 px-2 py-2">
                <UserMenu fallbackName={displayName} />
              </div>
            </div>
          </motion.aside>
        )}
      </AnimatePresence>
      {!sidebarOpen &&
        !pathname.startsWith("/workbench") &&
        !pathname.startsWith("/code") &&
        !pathname.startsWith("/design") && (
          <div className="absolute left-2 top-2 z-10">
            <Button
              variant="ghost"
              size="icon"
              onClick={toggleSidebar}
              aria-label="Open sidebar"
              className="size-8"
            >
              <PanelLeftOpen className="size-4" />
            </Button>
          </div>
        )}
    </>
  );
}

// Searchable + grouped Recents list.
// - Cmd/Ctrl+K focuses the filter input
// - Conversations bucketed by Today / Yesterday / Last 7 days / Older
// - Double-click a row to rename it inline (Enter to save, Esc to cancel)
function RecentsList({
  conversations,
  isLoading,
  pathname,
  label,
}: {
  conversations: ConversationSummary[] | undefined;
  isLoading: boolean;
  pathname: string;
  label: string;
}) {
  const [filter, setFilter] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  // Cmd/Ctrl+K → focus the filter. Skip when the user is already
  // typing in another field so the binding doesn't steal focus from
  // the composer mid-message.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const isMac = /Mac|iPhone|iPad/.test(navigator.platform);
      const mod = isMac ? e.metaKey : e.ctrlKey;
      if (!mod || e.key !== "k") return;
      const target = e.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable)
      ) {
        // Composer/Search input has focus already — don't fight it.
        if (target !== inputRef.current) return;
      }
      e.preventDefault();
      inputRef.current?.focus();
      inputRef.current?.select();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const filtered = useMemo(() => {
    if (!conversations) return [];
    const q = filter.trim().toLowerCase();
    if (!q) return conversations;
    return conversations.filter((c) => c.title.toLowerCase().includes(q));
  }, [conversations, filter]);

  const grouped = useMemo(() => {
    const out: Record<Bucket, ConversationSummary[]> = {
      today: [],
      yesterday: [],
      week: [],
      older: [],
    };
    for (const c of filtered) out[bucketOf(c.updatedAt)].push(c);
    return out;
  }, [filtered]);

  return (
    <div className="mt-5 flex-1 overflow-y-auto px-2">
      <div className="flex items-center gap-1.5 px-2.5 pb-1">
        <span className="flex-1 text-[11px] text-sidebar-foreground/50">
          {label}
        </span>
        {conversations && conversations.length > 0 && (
          <kbd className="hidden sm:inline-flex h-4 items-center justify-center rounded border border-border/50 bg-card/40 px-1 text-[9px] text-sidebar-foreground/50">
            ⌘K
          </kbd>
        )}
      </div>

      {conversations && conversations.length > 0 && (
        <div className="px-2.5 pb-2">
          <input
            ref={inputRef}
            type="text"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter chats…"
            className="w-full rounded-md border border-transparent bg-card/40 px-2 py-1 text-[12px] text-sidebar-foreground/85 placeholder:text-sidebar-foreground/40 outline-none focus:border-border focus:bg-card transition-colors"
          />
        </div>
      )}

      {isLoading && !conversations ? (
        <div className="px-2.5 py-1.5 text-xs text-sidebar-foreground/40">
          loading…
        </div>
      ) : !conversations || conversations.length === 0 ? (
        <div className="px-2.5 py-1.5 text-xs text-sidebar-foreground/40">
          no chats yet.
        </div>
      ) : filtered.length === 0 ? (
        <div className="px-2.5 py-1.5 text-xs text-sidebar-foreground/40">
          no matches.
        </div>
      ) : (
        (["today", "yesterday", "week", "older"] as Bucket[]).map((b) => {
          const items = grouped[b];
          if (items.length === 0) return null;
          return (
            <div key={b} className="mb-2">
              <div className="px-2.5 pb-0.5 pt-1 text-[10px] font-medium uppercase tracking-wider text-sidebar-foreground/40">
                {BUCKET_LABEL[b]}
              </div>
              <div className="space-y-px">
                {items.map((c) => (
                  <RecentRow key={c.id} c={c} pathname={pathname} />
                ))}
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}

// One conversation row. Single-click navigates; double-click swaps to
// an inline <input> so the user can rename without a context menu —
// matches Notion / VS Code / Finder rename UX. Enter saves, Esc
// cancels, blur saves (matches the discoverable convention).
function RecentRow({
  c,
  pathname,
}: {
  c: ConversationSummary;
  pathname: string;
}) {
  const href = `/chat/${c.id}`;
  const active = pathname === href;
  const isUntitled = !c.title.trim() || c.title === "New chat";
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(c.title);
  const rename = useRenameConversation();

  // Re-sync the draft if the title changed under us (e.g. server
  // auto-named the chat after first message).
  useEffect(() => {
    if (!editing) setDraft(c.title);
  }, [c.title, editing]);

  const commit = () => {
    const next = draft.trim();
    setEditing(false);
    if (!next || next === c.title) return;
    rename.mutate({ id: c.id, title: next });
  };

  if (editing) {
    return (
      <input
        autoFocus
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit();
          } else if (e.key === "Escape") {
            e.preventDefault();
            setDraft(c.title);
            setEditing(false);
          }
        }}
        onBlur={commit}
        className="block w-full truncate rounded-md border border-border bg-card px-2.5 py-1 text-[13px] leading-6 text-sidebar-accent-foreground outline-none focus:border-primary"
      />
    );
  }

  return (
    <Link
      href={href}
      onDoubleClick={(e) => {
        e.preventDefault();
        setEditing(true);
      }}
      className={cn(
        "block truncate rounded-md px-2.5 py-1 text-[13px] leading-6 transition-colors",
        "hover:bg-sidebar-accent/60",
        active
          ? "bg-sidebar-accent text-sidebar-accent-foreground"
          : isUntitled
            ? "text-sidebar-foreground/40"
            : "text-sidebar-foreground/85",
      )}
      title="Double-click to rename"
    >
      {isUntitled ? "Untitled" : c.title}
    </Link>
  );
}
