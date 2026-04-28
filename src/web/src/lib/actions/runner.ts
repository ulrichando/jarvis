"use client";

// Client-side action runner. Receives action objects from the streaming
// message parser and dispatches them:
//   - file actions  → PUT /api/workspace/<id>/file (final content only)
//   - shell actions → POST /api/workspace/<id>/exec (sync, awaited)
//   - start actions → POST /api/workspace/<id>/exec with detach=true
//
// The runner serializes actions per artifact: a shell action only fires
// after every preceding action in the same artifact has completed. This
// matters because bolt-style scripts assume strict ordering (write
// package.json → npm install → write source → start dev).

import type { Action, TrackedAction } from "./types";
import { apiWriteFile } from "@/lib/workspace/client";

export type ActionEvent =
  | { kind: "queued"; tracked: TrackedAction }
  | { kind: "running"; tracked: TrackedAction }
  | { kind: "success"; tracked: TrackedAction; output?: string }
  | { kind: "error"; tracked: TrackedAction; error: string };

export type ActionListener = (ev: ActionEvent) => void;

type Pending = {
  artifactId: string;
  actionId: string;
  action: Action;
  resolve: () => void;
};

export class ActionRunner {
  private workspaceId: string;
  private listener: ActionListener;
  private queues = new Map<string, Promise<void>>(); // per-artifact serial queue
  private fileBuffers = new Map<string, string>();    // actionId → live content
  private done = new Set<string>();                   // actionIds already executed

  constructor(workspaceId: string, listener: ActionListener) {
    this.workspaceId = workspaceId;
    this.listener = listener;
  }

  /**
   * Buffer the in-progress text of a streaming file action so the UI can
   * preview it. Does NOT write to disk yet — disk write happens in
   * onActionClose.
   */
  onStream(artifactId: string, actionId: string, action: Action) {
    if (action.type !== "file") return;
    this.fileBuffers.set(actionId, action.content);
    this.listener({
      kind: "running",
      tracked: { artifactId, actionId, action, status: "running" },
    });
  }

  /**
   * The action's closing tag arrived. Enqueue it on the artifact's serial
   * queue and execute when prior actions complete.
   */
  onClose(artifactId: string, actionId: string, action: Action) {
    if (this.done.has(actionId)) return;
    this.done.add(actionId);
    const tracked: TrackedAction = { artifactId, actionId, action, status: "queued" };
    this.listener({ kind: "queued", tracked });

    const prev = this.queues.get(artifactId) ?? Promise.resolve();
    const next = prev
      .then(() => this.execute(tracked))
      .catch((err) => {
        // Swallow per-action errors so the queue keeps draining; we still
        // emit `error` events to the listener.
        console.error("[action-runner] execution error:", err);
      });
    this.queues.set(artifactId, next);
  }

  private async execute(tracked: TrackedAction) {
    const { action } = tracked;
    this.listener({ kind: "running", tracked: { ...tracked, status: "running" } });

    try {
      if (action.type === "file") {
        await apiWriteFile(this.workspaceId, action.filePath, action.content);
        this.listener({
          kind: "success",
          tracked: { ...tracked, status: "success" },
        });
        return;
      }

      if (action.type === "shell") {
        const r = await fetch(`/api/workspace/${this.workspaceId}/exec`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ command: action.content }),
        });
        const j = await r.json();
        if (!r.ok || j.exitCode !== 0) {
          const message =
            j?.stderr?.trim() ||
            j?.error ||
            `exited with code ${j?.exitCode ?? "?"}`;
          this.listener({
            kind: "error",
            tracked: { ...tracked, status: "error", error: message },
            error: message,
          });
          return;
        }
        this.listener({
          kind: "success",
          tracked: { ...tracked, status: "success" },
          output: j.stdout,
        });
        return;
      }

      if (action.type === "start") {
        const r = await fetch(`/api/workspace/${this.workspaceId}/exec`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ command: action.content, detach: true }),
        });
        const j = await r.json();
        if (!r.ok) {
          const message = j?.error ?? "failed to start";
          this.listener({
            kind: "error",
            tracked: { ...tracked, status: "error", error: message },
            error: message,
          });
          return;
        }
        this.listener({
          kind: "success",
          tracked: { ...tracked, status: "success" },
        });
        return;
      }
    } catch (e) {
      const message = (e as Error).message ?? "unknown error";
      this.listener({
        kind: "error",
        tracked: { ...tracked, status: "error", error: message },
        error: message,
      });
    }
  }
}
