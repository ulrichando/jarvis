"use client";

import { create } from "zustand";

// Tracks which files JARVIS has recently written into each workspace, so
// the FileTree can paint a "just edited" marker next to them. Lives only
// in memory — refreshing the page clears the markers, which is the
// behavior we want (the indicators are meant for "see the edit happen
// live", not a long-term log).
//
// Why a dedicated store: the chat layer writes file paths during
// streaming, and the FileTree is in a different React subtree. A shared
// Zustand store decouples them without prop drilling.
//
// The shape is keyed by workspaceId so multi-workspace navigation
// doesn't bleed indicators across projects.
type Edits = Map<string, number>; // path → timestamp(ms)

type State = {
  byWorkspace: Map<string, Edits>;
  markEdited: (workspaceId: string, path: string) => void;
  clearWorkspace: (workspaceId: string) => void;
  // Helper: returns true if `path` in `workspaceId` was edited within
  // `withinMs` (default 60s). UI components use this for live markers.
  wasRecentlyEdited: (
    workspaceId: string,
    path: string,
    withinMs?: number,
  ) => boolean;
};

export const useEditedFiles = create<State>((set, get) => ({
  byWorkspace: new Map(),
  markEdited: (workspaceId, path) =>
    set((s) => {
      const next = new Map(s.byWorkspace);
      const inner = new Map(next.get(workspaceId) ?? []);
      inner.set(path, Date.now());
      next.set(workspaceId, inner);
      return { byWorkspace: next };
    }),
  clearWorkspace: (workspaceId) =>
    set((s) => {
      if (!s.byWorkspace.has(workspaceId)) return s;
      const next = new Map(s.byWorkspace);
      next.delete(workspaceId);
      return { byWorkspace: next };
    }),
  wasRecentlyEdited: (workspaceId, path, withinMs = 60_000) => {
    const inner = get().byWorkspace.get(workspaceId);
    if (!inner) return false;
    const ts = inner.get(path);
    if (!ts) return false;
    return Date.now() - ts <= withinMs;
  },
}));
