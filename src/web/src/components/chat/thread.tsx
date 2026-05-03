"use client";

import { useEffect, useRef } from "react";
import type { UIMessage } from "ai";
import { Message } from "./message";

export function Thread({
  messages,
  isStreaming,
  artifactPanel,
  reasoningById,
  planById,
  usageById,
  workspaceId,
}: {
  messages: UIMessage[];
  isStreaming: boolean;
  artifactPanel?: React.ReactNode;
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
  // When set, each assistant message gets an Undo button that rolls
  // the workspace back to the snapshot taken just before that turn.
  workspaceId?: string;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, isStreaming]);

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-8">
      {messages.map((m, i) => (
        <Message
          key={m.id}
          message={m}
          isStreaming={
            isStreaming && i === messages.length - 1 && m.role === "assistant"
          }
          reasoning={reasoningById?.get(m.id)}
          plan={planById?.get(m.id)}
          usage={usageById?.get(m.id)}
          workspaceId={workspaceId}
        />
      ))}
      {artifactPanel}
      <div ref={bottomRef} className="h-4" />
    </div>
  );
}
