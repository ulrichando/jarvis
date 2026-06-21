import { create } from "zustand";

// Tracks which assistant message voice mode is currently reading aloud, and how
// far the TTS has spoken (char index). message.tsx subscribes with a per-message
// selector so only the message being read re-renders on each word boundary.
type VoiceReadState = {
  readingId: string | null;
  readChar: number;
  startReading: (id: string) => void;
  setChar: (n: number) => void;
  stopReading: () => void;
};

export const useVoiceRead = create<VoiceReadState>((set) => ({
  readingId: null,
  readChar: 0,
  startReading: (id) => set({ readingId: id, readChar: 0 }),
  setChar: (n) => set({ readChar: n }),
  stopReading: () => set({ readingId: null, readChar: 0 }),
}));
