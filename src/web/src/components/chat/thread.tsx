"use client";

import { useEffect, useMemo, useRef } from "react";
import type { UIMessage } from "ai";
import { Message } from "./message";
import { VerifyPill } from "./verify-pill";
import type { ArtifactData, TrackedAction } from "@/lib/actions/types";

type ArtifactCard = {
  artifact: ArtifactData;
  actions: TrackedAction[];
  messageId: string;
};

// Canary for the chat layer's auto-continue synthetic user prompt. The
// chat client sends this when streamText finishes with `length` so the
// next call extends the partial reply. Each auto-continue creates a
// SEPARATE assistant DB row, which makes the conversation look like
// two messages on refresh — and the second one starts mid-JSX
// (`max-w-3xl mx-auto">` instead of clean prose). The `coalesceTurns`
// preprocessor below detects this synthetic-prompt pattern and folds
// the two halves back into one logical assistant turn before render.
const AUTO_CONTINUE_CANARY = "Continue your previous output exactly where you stopped";

function isSyntheticAutoContinue(m: UIMessage): boolean {
  if (m.role !== "user") return false;
  const text = m.parts
    .map((p) => (p.type === "text" ? p.text : ""))
    .join("")
    .trim();
  return (
    text.startsWith(AUTO_CONTINUE_CANARY) &&
    text.includes("Close any open boltAction")
  );
}

/**
 * Fold auto-continue boundary into one assistant turn. The chat route
 * persists each streamText completion as its own assistant row — fine
 * for normal turns, but auto-continue creates two assistant rows with
 * a synthetic user prompt sandwiched between them. Coalesce: when we
 * see [assistant, synthetic-user, assistant], merge the trailing
 * assistant's text onto the leading one and drop the middle two.
 *
 * Pure presentation transform — DB is unchanged. Idempotent (running
 * twice gives the same result).
 */
function coalesceTurns(messages: UIMessage[]): {
  visible: UIMessage[];
  // Map of merged-message id → list of original ids that were folded
  // into it. Lets per-message lookups (artifacts, reasoning, plan)
  // union across all the original turn ids when an auto-continue
  // boundary collapsed two assistant rows into one.
  foldedIds: Map<string, string[]>;
} {
  const out: UIMessage[] = [];
  const folded = new Map<string, string[]>();
  for (let i = 0; i < messages.length; i++) {
    const m = messages[i];
    if (
      m.role === "assistant" &&
      i + 2 < messages.length &&
      isSyntheticAutoContinue(messages[i + 1]) &&
      messages[i + 2].role === "assistant"
    ) {
      const next = messages[i + 2];
      const merged: UIMessage = {
        ...m,
        parts: [
          {
            type: "text",
            text:
              m.parts.map((p) => (p.type === "text" ? p.text : "")).join("") +
              next.parts.map((p) => (p.type === "text" ? p.text : "")).join(""),
          },
        ],
      };
      out.push(merged);
      folded.set(merged.id, [m.id, next.id]);
      i += 2;
      continue;
    }
    if (isSyntheticAutoContinue(m)) continue;
    out.push(m);
  }
  return { visible: out, foldedIds: folded };
}

export function Thread({
  messages,
  isStreaming,
  artifacts,
  renderArtifacts,
  renderJarvisCards,
  reasoningById,
  planById,
  usageById,
  errorById,
  verifyById,
  onRetry,
  onVerifyRetry,
  workspaceId,
  isAtBottom = true,
}: {
  messages: UIMessage[];
  isStreaming: boolean;
  // Conversation-wide artifact map (keyed by artifact id). Each card
  // carries its source `messageId`; Thread filters per turn so each
  // artifact renders under the assistant message that produced it,
  // not in a single bottom panel that would appear to belong to
  // every later turn.
  artifacts?: Map<string, ArtifactCard>;
  // Render-prop wrapping the per-message ArtifactPanel slice. Lets
  // chat.tsx inject the workspace-level props (workspaceId, name,
  // previewPort, embedded) once; Thread provides the filtered cards.
  renderArtifacts?: (cards: Map<string, ArtifactCard>) => React.ReactNode;
  // claude.ai-style inline card(s) for System B (<jarvisArtifact>) artifacts
  // a turn produced — clickable to open the side panel. Keyed by message id.
  renderJarvisCards?: (messageId: string) => React.ReactNode;
  reasoningById?: Map<string, string>;
  planById?: Map<string, { content: string; complete: boolean }>;
  usageById?: Map<
    string,
    {
      inputTokens: number;
      outputTokens: number;
      reasoningTokens?: number;
      model?: string;
    }
  >;
  // Map of failed assistant message ids → error label. Drives the
  // inline "Response stopped — Retry" pill in the Message component.
  errorById?: Map<string, string>;
  // Per-message verify outcome (tsc / curl / screenshot). When present,
  // the message renders a VerifyPill so the user has a visible signal
  // that verification ran + can manually click "Try fix" if it failed.
  // Replaces the old synthetic [auto-retry] prompt that used to appear
  // in the chat as if the user had typed it.
  verifyById?: Map<string, import("@/lib/verify/types").VerifyOutcome>;
  // Invoked when the user clicks the Retry pill on a failed message.
  // Receives the failed assistant's id; the Chat component re-submits
  // the prior user turn.
  onRetry?: (messageId: string) => void;
  // Invoked when the user clicks "Try fix" on the VerifyPill of a
  // failed-verify assistant message.
  onVerifyRetry?: (messageId: string) => void;
  // When set, each assistant message gets an Undo button that rolls
  // the workspace back to the snapshot taken just before that turn.
  workspaceId?: string;
  // When false, the user has scrolled up to read history — auto-scroll
  // PAUSES so the page doesn't yank them back to the bottom on every
  // streaming token. The parent's scroll-to-bottom pill gives them a
  // one-click way to re-attach. Defaults to true so callers that
  // don't manage stickiness preserve the legacy auto-scroll behavior.
  isAtBottom?: boolean;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);
  // Previous count of USER messages. When it grows, the user just
  // submitted a new prompt — force-scroll to the bottom regardless
  // of `isAtBottom`. Without this override, the new user bubble +
  // assistant placeholder pop the height out of the 70px stickiness
  // threshold BEFORE the effect runs, isAtBottom flips to false,
  // and the auto-scroll gets gated out — making it look like the
  // chat "stopped scrolling on new prompts." Streaming tokens still
  // honor the gate so the page doesn't yank a reader back when
  // they've scrolled up to read history.
  const prevUserCountRef = useRef(0);

  useEffect(() => {
    const userCount = messages.reduce(
      (n, m) => (m.role === "user" ? n + 1 : n),
      0,
    );
    const userJustSubmitted = userCount > prevUserCountRef.current;
    prevUserCountRef.current = userCount;
    if (userJustSubmitted) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
      return;
    }
    // Otherwise only force-scroll when the user is "stuck" to the
    // bottom. If they've scrolled up to look at earlier turns,
    // leave them alone.
    if (!isAtBottom) return;
    // This effect re-fires on every streamed token — `messages` changes
    // on each rAF flush (~60×/s). Smooth-scrolling per token stacks
    // animations and stutters (the visible jank), so scroll INSTANTLY
    // while streaming and reserve smooth for discrete idle height changes.
    bottomRef.current?.scrollIntoView({
      behavior: isStreaming ? "auto" : "smooth",
      block: "end",
    });
  }, [messages, isStreaming, isAtBottom]);

  // Apply the auto-continue coalesce ONCE per messages-array reference
  // change. Pure transform, no side-effects.
  const { visible, foldedIds } = useMemo(
    () => coalesceTurns(messages),
    [messages],
  );

  // Bucket artifacts by their source message id (unioning ids when an
  // auto-continue boundary folded two assistant rows into one merged
  // turn). Result: per-message Map<artifactId, ArtifactCard> ready to
  // hand off to the renderArtifacts prop.
  const artifactsByMessage = useMemo(() => {
    const out = new Map<string, Map<string, ArtifactCard>>();
    if (!artifacts || artifacts.size === 0) return out;
    // Build reverse map: original id → display id (post-coalesce).
    const displayIdOf = new Map<string, string>();
    for (const m of visible) {
      displayIdOf.set(m.id, m.id);
      const folded = foldedIds.get(m.id);
      if (folded) for (const fid of folded) displayIdOf.set(fid, m.id);
    }
    for (const card of artifacts.values()) {
      const dispId = displayIdOf.get(card.messageId);
      if (!dispId) continue; // orphan artifact (shouldn't happen post-rehydrate)
      let bucket = out.get(dispId);
      if (!bucket) {
        bucket = new Map();
        out.set(dispId, bucket);
      }
      bucket.set(card.artifact.id, card);
    }
    return out;
  }, [artifacts, visible, foldedIds]);

  return (
    // space-y-8 per Claude/ChatGPT — generic chat UIs ship at space-y-2
    // which packs the thread too tight. The body line-height bump
    // (text-[15px] leading-7 → leading-relaxed in Markdown) is the
    // other half of the breathing-room fix.
    // Responsive width with a fixed maximum, matching industry standard:
    // Claude.ai (768px), ChatGPT (768px at xl), Perplexity (~768px) all
    // cap at max-w-3xl and DO NOT grow on wide displays. Going past ~768px
    // pushes line length above the readability sweet spot. Embedded chat
    // surfaces (design column, workbench panel) inherit the cap but the
    // parent panel is narrower, so the actual rendered width = parent.
    // Spacing rides --chat-gap / --chat-pad-y (Settings → Appearance →
    // Density, set via data-chat-density on the chat root); 2rem = "cozy".
    <div className="mx-auto w-full max-w-3xl space-y-[var(--chat-gap,2rem)] px-4 py-[var(--chat-pad-y,2rem)]">
      {visible.map((m, i) => {
        const cards = artifactsByMessage.get(m.id);
        // Verify outcomes are keyed by the streaming assistantId. When
        // an auto-continue boundary collapsed two assistant rows into
        // one display turn, look up under any of the folded ids too.
        const verifyOutcome = (() => {
          if (!verifyById) return undefined;
          const direct = verifyById.get(m.id);
          if (direct) return direct;
          const folded = foldedIds.get(m.id);
          if (!folded) return undefined;
          for (const fid of folded) {
            const v = verifyById.get(fid);
            if (v) return v;
          }
          return undefined;
        })();
        return (
          <div key={m.id} className="space-y-3">
            <Message
              message={m}
              isStreaming={
                isStreaming && i === visible.length - 1 && m.role === "assistant"
              }
              reasoning={reasoningById?.get(m.id)}
              plan={planById?.get(m.id)}
              usage={usageById?.get(m.id)}
              error={errorById?.get(m.id)}
              onRetry={onRetry}
              workspaceId={workspaceId}
              isLast={i === visible.length - 1}
            />
            {cards && cards.size > 0 && renderArtifacts
              ? renderArtifacts(cards)
              : null}
            {m.role === "assistant" && renderJarvisCards
              ? renderJarvisCards(m.id)
              : null}
            {verifyOutcome && m.role === "assistant" && (
              <VerifyPill
                outcome={verifyOutcome}
                onRetry={
                  onVerifyRetry && !verifyOutcome.ok
                    ? () => onVerifyRetry(m.id)
                    : undefined
                }
              />
            )}
          </div>
        );
      })}
      <div ref={bottomRef} className="h-4" />
    </div>
  );
}
