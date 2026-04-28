"use client";

// Self-diagnostic raw chat. No SDK abstraction. Every state transition
// and stream event is visible on-page so we can see exactly where it
// breaks without opening DevTools.

import { useState, useRef } from "react";

type Msg = { id: string; role: "user" | "assistant"; text: string };
type Trace = { t: number; tag: string; detail: string };

export default function ChatRawPage() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [trace, setTrace] = useState<Trace[]>([]);
  const startRef = useRef(0);

  const log = (tag: string, detail = "") => {
    setTrace((prev) => [
      ...prev,
      { t: Date.now() - startRef.current, tag, detail },
    ]);
  };

  const send = async () => {
    const content = input.trim();
    if (!content || streaming) return;
    startRef.current = Date.now();
    setTrace([]);
    log("send-clicked", `content="${content}"`);

    const userMsg: Msg = { id: `u-${Date.now()}`, role: "user", text: content };
    const assistantId = `a-${Date.now()}`;
    setMessages((m) => {
      const next = [
        ...m,
        userMsg,
        { id: assistantId, role: "assistant" as const, text: "" },
      ];
      log("setMessages-after-send", `next.length=${next.length}`);
      return next;
    });
    setInput("");
    setStreaming(true);
    log("streaming=true");

    try {
      log("fetch-start");
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          id: "chat-raw",
          model: "llama-3.3-70b",
          messages: [
            ...messages.map((m) => ({
              id: m.id,
              role: m.role,
              parts: [{ type: "text", text: m.text }],
            })),
            { id: userMsg.id, role: "user", parts: [{ type: "text", text: content }] },
          ],
        }),
      });

      log("fetch-response", `status=${res.status} hasBody=${!!res.body}`);

      if (!res.ok || !res.body) {
        const text = await res.text();
        log("fetch-bad", text.slice(0, 200));
        throw new Error(`HTTP ${res.status}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let chunkCount = 0;
      let textDeltaCount = 0;

      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          log("stream-done", `chunks=${chunkCount} textDeltas=${textDeltaCount}`);
          break;
        }
        chunkCount++;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6);
          if (raw === "[DONE]") {
            log("DONE-event");
            continue;
          }
          let evt: { type?: string; delta?: string };
          try {
            evt = JSON.parse(raw);
          } catch {
            continue;
          }
          if (evt.type === "text-delta" && typeof evt.delta === "string") {
            textDeltaCount++;
            const chunk = evt.delta;
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, text: m.text + chunk } : m,
              ),
            );
            // Log first 3 deltas to confirm setState is firing
            if (textDeltaCount <= 3) {
              log("text-delta", `#${textDeltaCount} "${chunk}"`);
            }
          }
        }
      }
    } catch (e) {
      log("error", (e as Error).message);
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? { ...m, text: `(error: ${(e as Error).message})` }
            : m,
        ),
      );
    } finally {
      setStreaming(false);
      log("streaming=false");
    }
  };

  return (
    <div style={{ padding: 24, fontFamily: "system-ui", color: "#ddd", minHeight: "100vh" }}>
      <h1 style={{ fontSize: 18, marginBottom: 8 }}>chat-raw (self-diagnostic)</h1>
      <div style={{ color: "#888", marginBottom: 16, fontSize: 13 }}>
        messages: <strong style={{ color: "#fff" }}>{messages.length}</strong>
        {" · "}
        streaming: <strong style={{ color: streaming ? "#5af" : "#888" }}>{streaming ? "yes" : "no"}</strong>
      </div>

      <div style={{ marginBottom: 16, minHeight: 100 }}>
        <h2 style={{ fontSize: 14, color: "#888", marginBottom: 8 }}>messages</h2>
        {messages.length === 0 ? (
          <em style={{ color: "#666" }}>(no messages yet)</em>
        ) : (
          messages.map((m) => (
            <div
              key={m.id}
              style={{
                margin: "8px 0",
                padding: 12,
                background: m.role === "user" ? "#1a3a5a" : "#1a1a1a",
                borderRadius: 6,
                whiteSpace: "pre-wrap",
              }}
            >
              <div style={{ fontSize: 11, color: "#888", marginBottom: 4 }}>
                {m.role} · id={m.id} · text.length={m.text.length}
              </div>
              <div style={{ minHeight: 18 }}>
                {m.text || <em style={{ color: "#666" }}>(empty)</em>}
              </div>
            </div>
          ))
        )}
      </div>

      <div style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 14, color: "#888", marginBottom: 8 }}>
          live trace ({trace.length} events)
        </h2>
        {trace.length === 0 ? (
          <em style={{ color: "#666" }}>(empty — no submit yet)</em>
        ) : (
          <div
            style={{
              fontFamily: "monospace",
              fontSize: 12,
              padding: 12,
              background: "#0a0a0a",
              border: "1px solid #333",
              borderRadius: 6,
              maxHeight: 240,
              overflowY: "auto",
            }}
          >
            {trace.map((t, i) => (
              <div key={i}>
                <span style={{ color: "#666" }}>+{t.t}ms</span>{" "}
                <span style={{ color: "#5af" }}>{t.tag}</span>
                {t.detail && (
                  <>
                    {"  "}
                    <span style={{ color: "#aaa" }}>{t.detail}</span>
                  </>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder="say something..."
          disabled={streaming}
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
          disabled={streaming || !input.trim()}
          style={{
            padding: "12px 20px",
            background: streaming ? "#444" : "#3a5a8a",
            color: "#fff",
            border: "none",
            borderRadius: 6,
            cursor: streaming ? "not-allowed" : "pointer",
          }}
        >
          {streaming ? "streaming…" : "Send"}
        </button>
      </div>
    </div>
  );
}
