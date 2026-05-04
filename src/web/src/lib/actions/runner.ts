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
import {
  apiCreateEntry,
  apiReadFile,
  apiWriteFile,
} from "@/lib/workspace/client";

export type ShellResult = {
  exitCode: number;
  stdout: string;
  stderr: string;
  // For start actions we don't wait — there's no exit yet. The chat
  // layer still needs to know the action was a "start" so it generates
  // a meaningful boltActionResult block ("started in background").
  detached?: boolean;
};

export type ActionEvent =
  | { kind: "queued"; tracked: TrackedAction }
  | { kind: "running"; tracked: TrackedAction }
  | {
      kind: "success";
      tracked: TrackedAction;
      output?: string;
      result?: ShellResult;
    }
  | {
      kind: "error";
      tracked: TrackedAction;
      error: string;
      result?: ShellResult;
    };

export type ActionListener = (ev: ActionEvent) => void;

/**
 * Fires after a file is successfully written to disk — placeholder OR
 * final-content write. The chat layer uses this to invalidate the
 * design-tree query so the file appears in the panel right away.
 *
 * Decoupled from ActionListener because placeholder writes shouldn't
 * push extra status transitions through the action-card UI.
 */
export type FileWriteListener = (filePath: string) => void;

type Pending = {
  artifactId: string;
  actionId: string;
  action: Action;
  resolve: () => void;
};

export class ActionRunner {
  private workspaceId: string;
  private listener: ActionListener;
  private onFileWrite?: FileWriteListener;
  private queues = new Map<string, Promise<void>>(); // per-artifact serial queue
  private fileBuffers = new Map<string, string>();    // actionId → live content
  private done = new Set<string>();                   // actionIds already executed
  private placeholders = new Set<string>();           // actionIds with a placeholder already written
  private dirsCreated = new Set<string>();            // folder paths we've already mkdir'd in this turn
  // Per-action snapshot of file content from BEFORE the placeholder
  // overwrite. Lets us restore the original if the model emits a
  // boltAction whose body is empty (a common truncation pattern: open
  // tag + close tag arrive but no chunks streamed in between, which
  // would otherwise nuke an existing file to 0 bytes and white-screen
  // the design preview). Only populated when the file existed with
  // non-empty content at onOpen time.
  private prevFileContent = new Map<string, string>();

  constructor(
    workspaceId: string,
    listener: ActionListener,
    onFileWrite?: FileWriteListener,
  ) {
    this.workspaceId = workspaceId;
    this.listener = listener;
    this.onFileWrite = onFileWrite;
  }

  /**
   * Wait for every per-artifact queue to settle. Used after the LLM's
   * stream finishes so the chat layer can capture all action results
   * before generating the <boltActionResults> follow-up block.
   */
  async drain(): Promise<void> {
    // Snapshot the queue promises (the map may grow if late close tags
    // race in) and await them. Re-check until stable.
    while (true) {
      const promises = Array.from(this.queues.values());
      if (promises.length === 0) return;
      await Promise.allSettled(promises);
      const after = Array.from(this.queues.values());
      if (after.length === promises.length) return;
    }
  }

  /**
   * Action's opening tag arrived. For file actions we want the file to
   * appear in the panel immediately — not 30 seconds later when the close
   * tag finally arrives. Write an empty placeholder via the per-artifact
   * queue so it's serialized with the eventual full-content write.
   *
   * Also: if the model crashes mid-file, the placeholder is what proves
   * the file was attempted, instead of leaving the user with nothing.
   */
  onOpen(artifactId: string, actionId: string, action: Action) {
    if (action.type !== "file") return;
    if (this.placeholders.has(actionId)) return;
    this.placeholders.add(actionId);
    const filePath = action.filePath;
    const prev = this.queues.get(artifactId) ?? Promise.resolve();
    const next = prev.then(async () => {
      try {
        // Snapshot the file's pre-placeholder content. If the model
        // emits an empty boltAction (truncated stream — open + close
        // arrive with no chunks in between), execute() restores this
        // snapshot instead of leaving the file at 0 bytes.
        try {
          const existing = await apiReadFile(this.workspaceId, filePath);
          if (existing.length > 0) {
            this.prevFileContent.set(actionId, existing);
          }
        } catch {
          /* file doesn't exist yet — nothing to preserve */
        }

        // Create ancestor folders explicitly before the placeholder file
        // so the design panel sees the folder appear FIRST, then the file
        // populate inside it on the next tick — instead of folder + file
        // both materializing together. Each newly-created ancestor fires
        // its own `onFileWrite` so the tree query invalidates between
        // steps. Already-created dirs are no-ops on the server (createEntry
        // uses mkdir -p semantics).
        const segments = filePath.split("/").slice(0, -1);
        if (segments.length > 0) {
          let cum = "";
          for (const seg of segments) {
            cum = cum ? `${cum}/${seg}` : seg;
            if (this.dirsCreated.has(cum)) continue;
            this.dirsCreated.add(cum);
            try {
              await apiCreateEntry(this.workspaceId, cum, "dir");
              this.onFileWrite?.(cum);
              // Tiny breathing room so the panel's polling cycle has a
              // chance to render the folder before the file inside it.
              await new Promise((r) => setTimeout(r, 80));
            } catch {
              /* dir may already exist — fine */
            }
          }
        }
        await apiWriteFile(this.workspaceId, filePath, "");
        this.onFileWrite?.(filePath);
      } catch (err) {
        // Swallow placeholder failures; the close-write will retry.
        console.warn("[action-runner] placeholder write failed:", err);
      }
    });
    this.queues.set(artifactId, next);
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
        // Empty-content guard: if the model emitted a boltAction with
        // no body (common when finish=length truncates the stream
        // between the open and close tags), don't nuke a previously-
        // populated file to 0 bytes. Restore the snapshot taken in
        // onOpen instead — the design preview stays alive while the
        // user can ask for a clean redo. Empty content for a file
        // that didn't previously exist is still allowed (legitimate
        // empty-file case like .gitignore stubs).
        if (action.content.length === 0) {
          const prev = this.prevFileContent.get(tracked.actionId);
          if (prev && prev.length > 0) {
            console.warn(
              `[action-runner] empty boltAction for ${action.filePath} — likely truncated stream. Restoring ${prev.length}-byte previous content instead of writing 0 bytes.`,
            );
            await apiWriteFile(this.workspaceId, action.filePath, prev);
            this.prevFileContent.delete(tracked.actionId);
            this.listener({
              kind: "success",
              tracked: { ...tracked, status: "success" },
            });
            return;
          }
        }
        await apiWriteFile(this.workspaceId, action.filePath, action.content);
        this.prevFileContent.delete(tracked.actionId);
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
        const result: ShellResult = {
          exitCode: typeof j?.exitCode === "number" ? j.exitCode : -1,
          stdout: typeof j?.stdout === "string" ? j.stdout : "",
          stderr: typeof j?.stderr === "string" ? j.stderr : "",
        };
        if (!r.ok || result.exitCode !== 0) {
          const message =
            result.stderr.trim() ||
            j?.error ||
            `exited with code ${result.exitCode}`;
          this.listener({
            kind: "error",
            tracked: { ...tracked, status: "error", error: message },
            error: message,
            result,
          });
          return;
        }
        this.listener({
          kind: "success",
          tracked: { ...tracked, status: "success" },
          output: result.stdout,
          result,
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
            result: { exitCode: -1, stdout: "", stderr: message, detached: true },
          });
          return;
        }
        this.listener({
          kind: "success",
          tracked: { ...tracked, status: "success" },
          result: {
            exitCode: 0,
            stdout: "",
            stderr: "",
            detached: true,
          },
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
