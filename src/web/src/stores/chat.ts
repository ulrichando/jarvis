import { create } from "zustand";
import { persist } from "zustand/middleware";
import { DEFAULT_MODEL, type ModelId } from "@/lib/ai/models-meta";

// Effort ladder mirrors claude.ai's picker: Low → Max. Maps server-side to
// per-provider reasoning controls (OpenAI reasoningEffort / Anthropic +
// Google thinking budgets) in /api/chat. "high" is the default.
export type Effort = "low" | "medium" | "high" | "extra" | "max";
export const EFFORTS: Effort[] = ["low", "medium", "high", "extra", "max"];
export const DEFAULT_EFFORT: Effort = "high";

type ChatState = {
  model: ModelId;
  setModel: (m: ModelId) => void;
  // Reasoning effort + explicit "thinking" toggle. Sent with every chat
  // request; the route maps them onto provider-specific reasoning options.
  effort: Effort;
  setEffort: (e: Effort) => void;
  thinking: boolean;
  setThinking: (t: boolean) => void;
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
      effort: DEFAULT_EFFORT,
      setEffort: (e) => set({ effort: e }),
      thinking: false,
      setThinking: (t) => set({ thinking: t }),
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
