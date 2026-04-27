import { create } from "zustand";
import { persist } from "zustand/middleware";
import { DEFAULT_MODEL, type ModelId } from "@/lib/ai/models-meta";

type ChatState = {
  model: ModelId;
  setModel: (m: ModelId) => void;
  // Optional workspace target. When set, AI gets workbench instructions
  // appended to its system prompt and any <boltAction> emitted gets
  // executed against this workspace's container.
  targetWorkspaceId: string | null;
  targetWorkspaceName: string | null;
  setTargetWorkspace: (id: string | null, name?: string | null) => void;
};

export const useChatStore = create<ChatState>()(
  persist(
    (set) => ({
      model: DEFAULT_MODEL,
      setModel: (m) => set({ model: m }),
      targetWorkspaceId: null,
      targetWorkspaceName: null,
      setTargetWorkspace: (id, name) =>
        set({ targetWorkspaceId: id, targetWorkspaceName: name ?? null }),
    }),
    {
      // Bumped from "jarvis-chat" to "-v2": prior persisted state may
      // hold a model id we don't have an API key for (e.g. older
      // default claude-sonnet-4-6), causing every submit to silently
      // fail with missing_api_key. New name forces a fresh default.
      name: "jarvis-chat-v2",
    },
  ),
);
