"use client";

// Bare-minimum chat page to isolate whether useChat itself works in
// this stack. No model picker, no error banner, no message actions,
// no Composer wrapper, no streaming-status indicators. Just textarea +
// send + render. If THIS page works, our regular /chat is breaking
// somewhere in the wrapping; if it doesn't, the bug is in useChat /
// transport / AI SDK 6 + Next 16 interaction.

import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport, type UIMessage } from "ai";
import { useState } from "react";

export default function ChatTestPage() {
  const [input, setInput] = useState("");

  const transport = new DefaultChatTransport({
    api: "/api/chat",
    prepareSendMessagesRequest: ({ id, messages, body }) => ({
      body: { id, messages, model: "llama-3.3-70b", ...body },
    }),
  });

  const { messages, sendMessage, status } = useChat({
    transport,
  });

  const send = () => {
    const text = input.trim();
    if (!text) return;
    setInput("");
    sendMessage({ text });
  };

  return (
    <div style={{ padding: 24, fontFamily: "monospace", color: "#ddd" }}>
      <h1 style={{ fontSize: 18, marginBottom: 16 }}>
        chat-test (minimal useChat probe)
      </h1>
      <p style={{ color: "#888", marginBottom: 16 }}>
        status: {status} · messages: {messages.length}
      </p>

      <div style={{ marginBottom: 16, minHeight: 200 }}>
        {messages.length === 0 ? (
          <em style={{ color: "#666" }}>(no messages yet)</em>
        ) : (
          messages.map((m: UIMessage) => (
            <div
              key={m.id}
              style={{
                margin: "8px 0",
                padding: 8,
                background: m.role === "user" ? "#1a3a5a" : "#1a1a1a",
                borderRadius: 6,
              }}
            >
              <strong>{m.role}:</strong>{" "}
              {m.parts.map((p, i) =>
                p.type === "text" ? <span key={i}>{p.text}</span> : null,
              )}
            </div>
          ))
        )}
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              send();
            }
          }}
          placeholder="say something..."
          style={{
            flex: 1,
            padding: 12,
            background: "#0a0a0a",
            color: "#ddd",
            border: "1px solid #333",
            borderRadius: 6,
          }}
        />
        <button
          onClick={send}
          style={{
            padding: "12px 20px",
            background: "#3a5a8a",
            color: "#fff",
            border: "none",
            borderRadius: 6,
            cursor: "pointer",
          }}
        >
          Send
        </button>
      </div>
    </div>
  );
}
