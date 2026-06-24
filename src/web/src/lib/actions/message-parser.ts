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

import type { Action, ActionType, ArtifactData, ArtifactKind, FileAction, ShellAction, StartAction } from "./types";

// Tag constants stored lowercase so case-insensitive matching is a
// single .toLowerCase() on the input. Some models emit `<boltArtifact>`,
// some emit `<boltartifact>`, some `<BoltArtifact>` — all should parse.
// Without case-insensitive matching, lowercase tags pass through as
// visible text and React's renderer warns about unknown HTML elements.
const ARTIFACT_TAG_OPEN = "<boltartifact";
const ARTIFACT_TAG_CLOSE = "</boltartifact>";
const ACTION_TAG_OPEN = "<boltaction";
const ACTION_TAG_CLOSE = "</boltaction>";
// JARVIS-specific. Wraps a one-shot plan block the model emits BEFORE the
// boltArtifact, so the user sees what's about to be built before file
// streaming starts. Content is markdown; we strip the tag from visible
// output and surface it as a card via callbacks instead.
const PLAN_TAG_OPEN = "<jarvisplan";
const PLAN_TAG_CLOSE = "</jarvisplan>";
// Synthetic block injected by the chat layer (NOT by the model) after
// every boltArtifact's actions complete. Carries the actual exit/stdout/
// stderr of each shell action so the LLM has ground truth on the next
// turn. We strip this from the visible message body — it's only there
// to be re-sent in the conversation history.
const RESULTS_TAG_OPEN = "<boltactionresults";
const RESULTS_TAG_CLOSE = "</boltactionresults>";
// JARVIS claude.ai-style self-contained artifact (System B). A single
// renderable unit (one React component / HTML page / SVG / mermaid /
// markdown doc / code snippet) — NOT a multi-file bolt build. Content
// streams live into the artifact side panel; the tag is stripped from
// visible prose (like jarvisPlan). Distinct prefix from <boltartifact>
// so System A's parsing path is byte-for-byte unchanged.
const JARVIS_ARTIFACT_TAG_OPEN = "<jarvisartifact";
const JARVIS_ARTIFACT_TAG_CLOSE = "</jarvisartifact>";

export type ArtifactCallbackData = ArtifactData & {
  messageId: string;
};

export type ActionCallbackData = {
  messageId: string;
  artifactId: string;
  actionId: string;
  action: Action;
};

export type PlanCallbackData = {
  messageId: string;
  content: string;
  complete: boolean;
};

export type JarvisArtifactCallbackData = {
  messageId: string;
  slug: string;
  title: string;
  kind: ArtifactKind;
  language?: string;
  content: string;
  complete: boolean;
};

export type ParserCallbacks = {
  onArtifactOpen?: (data: ArtifactCallbackData) => void;
  onArtifactClose?: (data: ArtifactCallbackData) => void;
  onActionOpen?: (data: ActionCallbackData) => void;
  onActionStream?: (data: ActionCallbackData) => void;
  onActionClose?: (data: ActionCallbackData) => void;
  onPlan?: (data: PlanCallbackData) => void;
  // System B self-contained artifacts (<jarvisArtifact>).
  onJarvisArtifactOpen?: (data: JarvisArtifactCallbackData) => void;
  onJarvisArtifactStream?: (data: JarvisArtifactCallbackData) => void;
  onJarvisArtifactClose?: (data: JarvisArtifactCallbackData) => void;
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
  insidePlan: boolean;
  insideResults: boolean;
  insideJarvisArtifact: boolean;
  artifactCounter: number;
  currentArtifact?: ArtifactData;
  currentJarvisArtifact?: { slug: string; title: string; kind: ArtifactKind; language?: string };
  currentAction: PartialAction;
  actionId: number;
  planContent: string;
  jarvisArtifactContent: string;
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
        insidePlan: false,
        insideResults: false,
        insideJarvisArtifact: false,
        artifactCounter: 0,
        currentAction: { type: "", content: "" },
        actionId: 0,
        planContent: "",
        jarvisArtifactContent: "",
      };
      this.#messages.set(messageId, state);
    }

    // Lowercased view for case-insensitive tag matching. Models are
    // inconsistent about casing — `<boltArtifact>`, `<boltartifact>`,
    // `<BoltArtifact>` all need to parse. We keep the original input for
    // attribute extraction (regex flag `i` already case-insensitive)
    // and content slicing.
    const lower = input.toLowerCase();

    let output = "";
    let i = state.position;

    while (i < input.length) {
      if (state.insideResults) {
        // Drop everything inside <boltActionResults>...</boltActionResults>
        // from the visible output. Block exists for the LLM's eyes only.
        const closeIndex = lower.indexOf(RESULTS_TAG_CLOSE, i);
        if (closeIndex !== -1) {
          state.insideResults = false;
          i = closeIndex + RESULTS_TAG_CLOSE.length;
          continue;
        }
        return commit(state, output, input.length);
      }

      if (state.insidePlan) {
        // Accumulate plan content until we see </jarvisplan>. Don't emit
        // any of it to the visible output — it's surfaced via onPlan
        // instead and rendered as a card in the message component.
        const closeIndex = lower.indexOf(PLAN_TAG_CLOSE, i);
        if (closeIndex !== -1) {
          state.planContent += input.slice(i, closeIndex);
          this.callbacks.onPlan?.({
            messageId,
            content: state.planContent,
            complete: true,
          });
          state.insidePlan = false;
          i = closeIndex + PLAN_TAG_CLOSE.length;
          continue;
        }
        // Tag not closed yet — emit a streaming update so the plan card
        // builds up live (same UX as file actions), then bail until the
        // next chunk arrives.
        state.planContent += input.slice(i);
        this.callbacks.onPlan?.({
          messageId,
          content: state.planContent,
          complete: false,
        });
        return commit(state, output, input.length);
      }

      if (state.insideJarvisArtifact) {
        // Accumulate the self-contained artifact body until </jarvisArtifact>.
        // Content is NEVER emitted to the visible output — it's surfaced via
        // the onJarvisArtifact* callbacks and rendered in the side panel.
        const meta = state.currentJarvisArtifact!;
        const closeIndex = lower.indexOf(JARVIS_ARTIFACT_TAG_CLOSE, i);
        const finalize = (raw: string): string => {
          // Markdown artifacts ARE markdown — keep their fences. Other
          // kinds get a stray wrapping ```fence``` stripped + entities
          // unescaped, exactly like file-action content.
          if (meta.kind === "markdown") return raw;
          return unescapeTags(stripCodeFence(raw));
        };
        if (closeIndex !== -1) {
          state.jarvisArtifactContent += input.slice(i, closeIndex);
          this.callbacks.onJarvisArtifactClose?.({
            messageId,
            slug: meta.slug,
            title: meta.title,
            kind: meta.kind,
            language: meta.language,
            content: finalize(state.jarvisArtifactContent).trim(),
            complete: true,
          });
          state.insideJarvisArtifact = false;
          state.currentJarvisArtifact = undefined;
          state.jarvisArtifactContent = "";
          i = closeIndex + JARVIS_ARTIFACT_TAG_CLOSE.length;
          continue;
        }
        // Tag not closed yet — stream the in-progress content so Preview
        // builds up live (same UX as file actions), then bail until the
        // next chunk arrives.
        state.jarvisArtifactContent += input.slice(i);
        this.callbacks.onJarvisArtifactStream?.({
          messageId,
          slug: meta.slug,
          title: meta.title,
          kind: meta.kind,
          language: meta.language,
          content: finalize(state.jarvisArtifactContent),
          complete: false,
        });
        return commit(state, output, input.length);
      }

      if (state.insideArtifact) {
        const artifact = state.currentArtifact!;

        if (state.insideAction) {
          const closeIndex = lower.indexOf(ACTION_TAG_CLOSE, i);
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
            } else if (action.type === "shell" || action.type === "start") {
              // Unescape `&lt;` / `&gt;` in shell + start command bodies
              // too. Some models (DeepSeek V4 in particular) defensively
              // XML-escape angle brackets inside <boltAction> content,
              // so a shell like `grep '<Link><a' ...` arrives as
              // `grep '&lt;Link&gt;&lt;a' ...` and matches nothing in
              // the workspace. Without unescape the autonomous fix loop
              // diagnoses, finds "NO_MATCHES", and stops.
              content = unescapeTags(content);
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
          const actionOpen = lower.indexOf(ACTION_TAG_OPEN, i);
          const artifactClose = lower.indexOf(ARTIFACT_TAG_CLOSE, i);

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
        // Could be the start of <boltArtifact …>, <jarvisPlan …>, or
        // <boltActionResults …>. Probe forward: as long as the running
        // prefix is a prefix of any of the known tags, keep going; on
        // exact match, transition; on no-match, treat as ordinary text.
        const longest = Math.max(
          ARTIFACT_TAG_OPEN.length,
          PLAN_TAG_OPEN.length,
          RESULTS_TAG_OPEN.length,
          JARVIS_ARTIFACT_TAG_OPEN.length,
        );
        let j = i;
        let probe = "";
        let consumed = false;
        while (j < input.length && probe.length < longest) {
          probe += lower[j];
          // Full match for boltArtifact: open the artifact.
          if (probe === ARTIFACT_TAG_OPEN) {
            const next = input[j + 1];
            if (next && next !== ">" && next !== " ") {
              // `<boltArtifactSomething` — not our tag.
              output += input.slice(i, j + 1);
              i = j + 1;
              consumed = true;
              break;
            }
            const tagEnd = input.indexOf(">", j);
            if (tagEnd === -1) return commit(state, output, i);
            const tag = input.slice(i, tagEnd + 1);
            const title = extractAttr(tag, "title") ?? "Artifact";
            const type = extractAttr(tag, "type");
            const id = `${messageId}-${state.artifactCounter++}`;
            const artifact: ArtifactData = { id, title, type };
            state.insideArtifact = true;
            state.currentArtifact = artifact;
            this.callbacks.onArtifactOpen?.({ messageId, ...artifact });
            i = tagEnd + 1;
            consumed = true;
            break;
          }
          // Full match for jarvisPlan: enter plan mode.
          if (probe === PLAN_TAG_OPEN) {
            const next = input[j + 1];
            if (next && next !== ">" && next !== " ") {
              output += input.slice(i, j + 1);
              i = j + 1;
              consumed = true;
              break;
            }
            const tagEnd = input.indexOf(">", j);
            if (tagEnd === -1) return commit(state, output, i);
            state.insidePlan = true;
            state.planContent = "";
            this.callbacks.onPlan?.({
              messageId,
              content: "",
              complete: false,
            });
            i = tagEnd + 1;
            consumed = true;
            break;
          }
          // Full match for boltActionResults: enter results-skip mode.
          if (probe === RESULTS_TAG_OPEN) {
            const next = input[j + 1];
            if (next && next !== ">" && next !== " ") {
              output += input.slice(i, j + 1);
              i = j + 1;
              consumed = true;
              break;
            }
            const tagEnd = input.indexOf(">", j);
            if (tagEnd === -1) return commit(state, output, i);
            state.insideResults = true;
            i = tagEnd + 1;
            consumed = true;
            break;
          }
          // Full match for jarvisArtifact: open a self-contained artifact.
          if (probe === JARVIS_ARTIFACT_TAG_OPEN) {
            const next = input[j + 1];
            if (next && next !== ">" && next !== " ") {
              // `<jarvisArtifactSomething` — not our tag.
              output += input.slice(i, j + 1);
              i = j + 1;
              consumed = true;
              break;
            }
            const tagEnd = input.indexOf(">", j);
            if (tagEnd === -1) return commit(state, output, i);
            const tag = input.slice(i, tagEnd + 1);
            const slug =
              extractAttr(tag, "slug") ?? extractAttr(tag, "id") ?? "artifact";
            const title = extractAttr(tag, "title") ?? "Artifact";
            const kindRaw = (extractAttr(tag, "kind") ?? "code").toLowerCase();
            const kind = (
              ["code", "markdown", "html", "react", "svg", "mermaid"].includes(
                kindRaw,
              )
                ? kindRaw
                : "code"
            ) as ArtifactKind;
            const language = extractAttr(tag, "language");
            state.insideJarvisArtifact = true;
            state.currentJarvisArtifact = { slug, title, kind, language };
            state.jarvisArtifactContent = "";
            this.callbacks.onJarvisArtifactOpen?.({
              messageId,
              slug,
              title,
              kind,
              language,
              content: "",
              complete: false,
            });
            i = tagEnd + 1;
            consumed = true;
            break;
          }
          // Probe is no longer a prefix of any known tag — bail.
          if (
            !ARTIFACT_TAG_OPEN.startsWith(probe) &&
            !PLAN_TAG_OPEN.startsWith(probe) &&
            !RESULTS_TAG_OPEN.startsWith(probe) &&
            !JARVIS_ARTIFACT_TAG_OPEN.startsWith(probe)
          ) {
            output += input.slice(i, j + 1);
            i = j + 1;
            consumed = true;
            break;
          }
          j++;
        }

        if (consumed) continue;

        // EOF mid-probe and the prefix could still complete next chunk.
        if (
          j === input.length &&
          (ARTIFACT_TAG_OPEN.startsWith(probe) ||
            PLAN_TAG_OPEN.startsWith(probe) ||
            RESULTS_TAG_OPEN.startsWith(probe) ||
            JARVIS_ARTIFACT_TAG_OPEN.startsWith(probe))
        ) {
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
