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
}: {
  messages: UIMessage[];
  isStreaming: boolean;
  artifactPanel?: React.ReactNode;
  reasoningById?: Map<string, string>;
  planById?: Map<string, { content: string; complete: boolean }>;
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
        />
      ))}
      {artifactPanel}
      <div ref={bottomRef} className="h-4" />
    </div>
  );
}
