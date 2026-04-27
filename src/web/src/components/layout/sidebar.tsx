"use client";

import { motion, AnimatePresence } from "motion/react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import {
  ChevronDown,
  Code2,
  MessagesSquare,
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Search,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useUI } from "@/stores/ui";
import { useChatStore } from "@/stores/chat";
import { useConversations } from "@/hooks/use-conversations";
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
  { href: "/workbench", label: "Workbench", icon: Code2 },
] as const;

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
                      const href = `/anthropic/${f.slug}`;
                      const active = pathname === href;
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
                            const href = `/anthropic/${f.slug}`;
                            const active = pathname === href;
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

              {/* Recents */}
              <div className="mt-5 flex-1 overflow-y-auto px-2">
                <div className="px-2.5 pb-1 text-[11px] text-sidebar-foreground/50">
                  {recentsLabel}
                </div>
                <div className="space-y-px">
                  {isLoading && !conversations ? (
                    <div className="px-2.5 py-1.5 text-xs text-sidebar-foreground/40">
                      loading…
                    </div>
                  ) : conversations && conversations.length > 0 ? (
                    conversations.map((c) => {
                      const href = `/chat/${c.id}`;
                      const active = pathname === href;
                      const isUntitled =
                        !c.title.trim() || c.title === "New chat";
                      return (
                        <Link
                          key={c.id}
                          href={href}
                          className={cn(
                            "block truncate rounded-md px-2.5 py-1 text-[13px] leading-6 transition-colors",
                            "hover:bg-sidebar-accent/60",
                            active
                              ? "bg-sidebar-accent text-sidebar-accent-foreground"
                              : isUntitled
                                ? "text-sidebar-foreground/40"
                                : "text-sidebar-foreground/85",
                          )}
                        >
                          {isUntitled ? "Untitled" : c.title}
                        </Link>
                      );
                    })
                  ) : (
                    <div className="px-2.5 py-1.5 text-xs text-sidebar-foreground/40">
                      no chats yet.
                    </div>
                  )}
                </div>
              </div>

              {/* User footer */}
              <div className="border-t border-border/50 px-2 py-2">
                <Link
                  href="/settings"
                  className="flex items-center gap-2.5 rounded-md px-2 py-1.5 transition-colors hover:bg-sidebar-accent/60"
                >
                  <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/20 font-mono text-[11px] font-semibold tracking-wider text-primary">
                    {initials(displayName)}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[13px] font-medium text-sidebar-foreground">
                      {displayName}
                    </div>
                    <div className="truncate text-[11px] text-sidebar-foreground/60">
                      Personal · local
                    </div>
                  </div>
                  <MoreHorizontal className="size-4 shrink-0 text-sidebar-foreground/50" />
                </Link>
              </div>
            </div>
          </motion.aside>
        )}
      </AnimatePresence>
      {!sidebarOpen && !pathname.startsWith("/workbench") && !pathname.startsWith("/code") && (
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
