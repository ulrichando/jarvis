import { create } from "zustand";
import { persist } from "zustand/middleware";

type Theme = "light" | "dark" | "system";

type UIState = {
  sidebarOpen: boolean;
  // Client-side filter applied to the history list in the sidebar.
  // Empty string = no filter. Deliberately NOT persisted — searches
  // are ephemeral.
  sidebarSearchQuery: string;
  theme: Theme;
  activeConversationId: string | null;
  draft: Record<string, string>;
  // Settings/Customize modal (claude.ai opens these as an overlay, not a
  // page). `settingsSection` is the nav id to land on. Ephemeral.
  settingsOpen: boolean;
  settingsSection: string | null;
  toggleSidebar: () => void;
  setSidebarOpen: (open: boolean) => void;
  setSidebarSearchQuery: (q: string) => void;
  setTheme: (theme: Theme) => void;
  setActiveConversation: (id: string | null) => void;
  setDraft: (conversationId: string, text: string) => void;
  openSettings: (section?: string) => void;
  closeSettings: () => void;
};

export const useUI = create<UIState>()(
  persist(
    (set) => ({
      sidebarOpen: true,
      sidebarSearchQuery: "",
      theme: "system",
      activeConversationId: null,
      draft: {},
      settingsOpen: false,
      settingsSection: null,
      toggleSidebar: () =>
        set((s) => ({ sidebarOpen: !s.sidebarOpen })),
      setSidebarOpen: (open) => set({ sidebarOpen: open }),
      setSidebarSearchQuery: (q) => set({ sidebarSearchQuery: q }),
      setTheme: (theme) => set({ theme }),
      setActiveConversation: (id) => set({ activeConversationId: id }),
      setDraft: (conversationId, text) =>
        set((s) => ({ draft: { ...s.draft, [conversationId]: text } })),
      openSettings: (section) =>
        set({ settingsOpen: true, settingsSection: section ?? "general" }),
      closeSettings: () => set({ settingsOpen: false }),
    }),
    {
      name: "jarvis-web-ui",
      partialize: (s) => ({
        sidebarOpen: s.sidebarOpen,
        theme: s.theme,
      }),
    },
  ),
);
