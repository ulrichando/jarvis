// Streaming XML action parser, ported from bolt.diy
// (app/lib/runtime/message-parser.ts) and trimmed: dropped supabase,
// quick-actions, and the artifact-element HTML factory — JARVIS renders
// artifacts via React components in the chat thread, not raw HTML.
//
// Operates on a token-by-token text stream. Each call to parse() resumes
// where the last call stopped; partial tags are buffered until the rest
// of the chunk arrives. File actions stream their content live (so the
// editor can update as the AI writes); shell/start actions only fire on
// the closing tag.
//
// Recognized tags:
//   <boltArtifact id="..." title="...">           — wraps a set of actions
//     <boltAction type="file" filePath="...">     — write/replace a file
//     <boltAction type="shell">                   — run a shell command
//     <boltAction type="start">                   — start dev server
//   </boltArtifact>

import type { Action, ActionType, ArtifactData, FileAction, ShellAction, StartAction } from "./types";

const ARTIFACT_TAG_OPEN = "<boltArtifact";
const ARTIFACT_TAG_CLOSE = "</boltArtifact>";
const ACTION_TAG_OPEN = "<boltAction";
const ACTION_TAG_CLOSE = "</boltAction>";

export type ArtifactCallbackData = ArtifactData & {
  messageId: string;
};

export type ActionCallbackData = {
  messageId: string;
  artifactId: string;
  actionId: string;
  action: Action;
};

export type ParserCallbacks = {
  onArtifactOpen?: (data: ArtifactCallbackData) => void;
  onArtifactClose?: (data: ArtifactCallbackData) => void;
  onActionOpen?: (data: ActionCallbackData) => void;
  onActionStream?: (data: ActionCallbackData) => void;
  onActionClose?: (data: ActionCallbackData) => void;
};

type PartialAction =
  | { type: "file"; filePath: string; content: string }
  | { type: "shell"; content: string }
  | { type: "start"; content: string }
  | { type: ""; content: string };

type MessageState = {
  position: number;
  insideArtifact: boolean;
  insideAction: boolean;
  artifactCounter: number;
  currentArtifact?: ArtifactData;
  currentAction: PartialAction;
  actionId: number;
};

function stripCodeFence(content: string): string {
  // The model sometimes wraps file content in ```lang ... ``` even though
  // the prompt asks it not to. Strip a leading/trailing fence if present.
  const m = content.match(/^\s*```\w*\n([\s\S]*?)\n\s*```\s*$/);
  return m ? m[1] : content;
}

function unescapeTags(content: string): string {
  return content.replace(/&lt;/g, "<").replace(/&gt;/g, ">");
}

export class StreamingMessageParser {
  #messages = new Map<string, MessageState>();

  constructor(private callbacks: ParserCallbacks = {}) {}

  reset() {
    this.#messages.clear();
  }

  resetMessage(messageId: string) {
    this.#messages.delete(messageId);
  }

  /**
   * Parse the cumulative input for a given message id. Returns the
   * "user-visible" portion — everything outside <boltArtifact> blocks —
   * so the caller can render plain prose around the artifact UI.
   */
  parse(messageId: string, input: string): string {
    let state = this.#messages.get(messageId);
    if (!state) {
      state = {
        position: 0,
        insideArtifact: false,
        insideAction: false,
        artifactCounter: 0,
        currentAction: { type: "", content: "" },
        actionId: 0,
      };
      this.#messages.set(messageId, state);
    }

    let output = "";
    let i = state.position;

    while (i < input.length) {
      if (state.insideArtifact) {
        const artifact = state.currentArtifact!;

        if (state.insideAction) {
          const closeIndex = input.indexOf(ACTION_TAG_CLOSE, i);
          const action = state.currentAction;

          if (closeIndex !== -1) {
            action.content += input.slice(i, closeIndex);
            let content = action.content.trim();

            if (action.type === "file") {
              if (!action.filePath.endsWith(".md")) {
                content = stripCodeFence(content);
                content = unescapeTags(content);
              }
              content += "\n";
            }
            action.content = content;

            this.callbacks.onActionClose?.({
              messageId,
              artifactId: artifact.id,
              actionId: String(state.actionId - 1),
              action: action as Action,
            });

            state.insideAction = false;
            state.currentAction = { type: "", content: "" };
            i = closeIndex + ACTION_TAG_CLOSE.length;
          } else {
            // Partial action: stream the in-progress content out so the
            // editor can update live as a file is being written.
            if (action.type === "file") {
              let streamingContent = input.slice(i);
              if (!action.filePath.endsWith(".md")) {
                streamingContent = stripCodeFence(streamingContent);
                streamingContent = unescapeTags(streamingContent);
              }
              this.callbacks.onActionStream?.({
                messageId,
                artifactId: artifact.id,
                actionId: String(state.actionId - 1),
                action: {
                  type: "file",
                  filePath: action.filePath,
                  content: streamingContent,
                } satisfies FileAction,
              });
            }
            break;
          }
        } else {
          const actionOpen = input.indexOf(ACTION_TAG_OPEN, i);
          const artifactClose = input.indexOf(ARTIFACT_TAG_CLOSE, i);

          if (actionOpen !== -1 && (artifactClose === -1 || actionOpen < artifactClose)) {
            const tagEnd = input.indexOf(">", actionOpen);
            if (tagEnd === -1) break;

            state.currentAction = parseActionTag(input.slice(actionOpen, tagEnd + 1));
            state.insideAction = true;
            this.callbacks.onActionOpen?.({
              messageId,
              artifactId: artifact.id,
              actionId: String(state.actionId++),
              action: state.currentAction as Action,
            });
            i = tagEnd + 1;
          } else if (artifactClose !== -1) {
            this.callbacks.onArtifactClose?.({ messageId, ...artifact });
            state.insideArtifact = false;
            state.currentArtifact = undefined;
            i = artifactClose + ARTIFACT_TAG_CLOSE.length;
          } else {
            break;
          }
        }
      } else if (input[i] === "<" && input[i + 1] !== "/") {
        // Could be the start of <boltArtifact …>; scan forward to confirm.
        let j = i;
        let probe = "";
        while (j < input.length && probe.length < ARTIFACT_TAG_OPEN.length) {
          probe += input[j];
          if (probe === ARTIFACT_TAG_OPEN) {
            const next = input[j + 1];
            // Reject `<boltArtifactSomething` that just happens to share the prefix.
            if (next && next !== ">" && next !== " ") {
              output += input.slice(i, j + 1);
              i = j + 1;
              break;
            }
            const tagEnd = input.indexOf(">", j);
            if (tagEnd === -1) {
              // Tag not finished yet — wait for next chunk.
              return commit(state, output, i);
            }
            const tag = input.slice(i, tagEnd + 1);
            const title = extractAttr(tag, "title") ?? "Artifact";
            const type = extractAttr(tag, "type");
            const id = `${messageId}-${state.artifactCounter++}`;
            const artifact: ArtifactData = { id, title, type };
            state.insideArtifact = true;
            state.currentArtifact = artifact;
            this.callbacks.onArtifactOpen?.({ messageId, ...artifact });
            i = tagEnd + 1;
            break;
          } else if (!ARTIFACT_TAG_OPEN.startsWith(probe)) {
            // Not a bolt tag at all; emit the chars and continue.
            output += input.slice(i, j + 1);
            i = j + 1;
            break;
          }
          j++;
        }

        // We hit EOF mid-probe — the prefix could still complete on the
        // next chunk, so stop here and don't emit `<` yet.
        if (j === input.length && ARTIFACT_TAG_OPEN.startsWith(probe)) {
          break;
        }
      } else {
        output += input[i];
        i++;
      }
    }

    return commit(state, output, i);
  }
}

function commit(state: MessageState, output: string, position: number): string {
  state.position = position;
  return output;
}

function parseActionTag(tag: string): PartialAction {
  const type = (extractAttr(tag, "type") ?? "") as ActionType | "";
  if (type === "file") {
    return {
      type: "file",
      filePath: extractAttr(tag, "filePath") ?? "",
      content: "",
    } satisfies FileAction;
  }
  if (type === "shell") return { type: "shell", content: "" } satisfies ShellAction;
  if (type === "start") return { type: "start", content: "" } satisfies StartAction;
  return { type: "", content: "" };
}

function extractAttr(tag: string, name: string): string | undefined {
  const m = tag.match(new RegExp(`${name}="([^"]*)"`, "i"));
  return m ? m[1] : undefined;
}
