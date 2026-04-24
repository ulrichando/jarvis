import { create } from "zustand";
import { persist } from "zustand/middleware";
import { DEFAULT_MODEL, type ModelId } from "@/lib/ai/models-meta";

type ChatState = {
  model: ModelId;
  setModel: (m: ModelId) => void;
};

export const useChatStore = create<ChatState>()(
  persist(
    (set) => ({
      model: DEFAULT_MODEL,
      setModel: (m) => set({ model: m }),
    }),
    { name: "jarvis-chat" },
  ),
);
