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
  toggleSidebar: () => void;
  setSidebarOpen: (open: boolean) => void;
  setSidebarSearchQuery: (q: string) => void;
  setTheme: (theme: Theme) => void;
  setActiveConversation: (id: string | null) => void;
  setDraft: (conversationId: string, text: string) => void;
};

export const useUI = create<UIState>()(
  persist(
    (set) => ({
      sidebarOpen: true,
      sidebarSearchQuery: "",
      theme: "system",
      activeConversationId: null,
      draft: {},
      toggleSidebar: () =>
        set((s) => ({ sidebarOpen: !s.sidebarOpen })),
      setSidebarOpen: (open) => set({ sidebarOpen: open }),
      setSidebarSearchQuery: (q) => set({ sidebarSearchQuery: q }),
      setTheme: (theme) => set({ theme }),
      setActiveConversation: (id) => set({ activeConversationId: id }),
      setDraft: (conversationId, text) =>
        set((s) => ({ draft: { ...s.draft, [conversationId]: text } })),
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
