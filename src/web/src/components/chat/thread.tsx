"use client";

import { useEffect, useRef } from "react";
import type { UIMessage } from "ai";
import { Message } from "./message";

export function Thread({
  messages,
  isStreaming,
  artifactPanel,
}: {
  messages: UIMessage[];
  isStreaming: boolean;
  artifactPanel?: React.ReactNode;
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
        />
      ))}
      {artifactPanel}
      <div ref={bottomRef} className="h-4" />
    </div>
  );
}
