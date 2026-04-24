import { create } from "zustand";
import { persist } from "zustand/middleware";

type Theme = "light" | "dark" | "system";

type UIState = {
  sidebarOpen: boolean;
  // Right-column preview pane (artifacts / rendered docs / code
  // output). Closed by default — opt-in so the chat gets the full
  // width when there's nothing to preview. Persisted so the user's
  // preference carries across sessions.
  previewOpen: boolean;
  // Client-side filter applied to the history list in the sidebar.
  // Empty string = no filter. Deliberately NOT persisted — searches
  // are ephemeral.
  sidebarSearchQuery: string;
  theme: Theme;
  activeConversationId: string | null;
  draft: Record<string, string>;
  toggleSidebar: () => void;
  setSidebarOpen: (open: boolean) => void;
  togglePreview: () => void;
  setPreviewOpen: (open: boolean) => void;
  setSidebarSearchQuery: (q: string) => void;
  setTheme: (theme: Theme) => void;
  setActiveConversation: (id: string | null) => void;
  setDraft: (conversationId: string, text: string) => void;
};

export const useUI = create<UIState>()(
  persist(
    (set) => ({
      sidebarOpen: true,
      previewOpen: false,
      sidebarSearchQuery: "",
      theme: "system",
      activeConversationId: null,
      draft: {},
      toggleSidebar: () =>
        set((s) => ({ sidebarOpen: !s.sidebarOpen })),
      setSidebarOpen: (open) => set({ sidebarOpen: open }),
      togglePreview: () =>
        set((s) => ({ previewOpen: !s.previewOpen })),
      setPreviewOpen: (open) => set({ previewOpen: open }),
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
        previewOpen: s.previewOpen,
        theme: s.theme,
      }),
    },
  ),
);
