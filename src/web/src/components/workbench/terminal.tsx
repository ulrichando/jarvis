"use client";

import { useEffect, useRef } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

type Props = {
  workspaceId: string;
};

// xterm.js connects to the standalone PTY sidecar (scripts/pty-server.mjs).
// Default URL ws://localhost:8772/pty; override via NEXT_PUBLIC_PTY_URL.

export function WorkbenchTerminal({ workspaceId }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const fitRef = useRef<FitAddon | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const term = new Terminal({
      fontFamily: "ui-monospace, SFMono-Regular, monospace",
      fontSize: 13,
      cursorBlink: true,
      theme: {
        background: "#0b0b0d",
        foreground: "#d4d4d4",
        cursor: "#d4d4d4",
      },
      convertEol: true,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current);
    try { fit.fit(); } catch {}
    termRef.current = term;
    fitRef.current = fit;

    // Derive WS URL from the current page so it works whether the user
    // hits localhost:3001 or the LAN IP. Override with NEXT_PUBLIC_PTY_URL
    // if you want to point at a remote PTY host.
    const wsUrl =
      process.env.NEXT_PUBLIC_PTY_URL ??
      `ws://${window.location.hostname}:8772/pty`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    let opened = false;

    ws.onopen = () => {
      opened = true;
      ws.send(
        JSON.stringify({
          type: "init",
          workspaceId,
          cols: term.cols,
          rows: term.rows,
        }),
      );
    };
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "output") term.write(msg.data);
        else if (msg.type === "exit") term.write(`\r\n[exited code=${msg.code}]\r\n`);
      } catch {}
    };
    ws.onerror = () => {
      term.write("\r\n\x1b[31m[pty connection error — is `npm run dev:pty` running?]\x1b[0m\r\n");
    };
    ws.onclose = () => {
      if (opened) term.write("\r\n[disconnected]\r\n");
    };

    term.onData((data) => {
      if (ws.readyState === ws.OPEN) {
        ws.send(JSON.stringify({ type: "input", data }));
      }
    });

    const onResize = () => {
      try {
        fit.fit();
        if (ws.readyState === ws.OPEN) {
          ws.send(
            JSON.stringify({
              type: "resize",
              cols: term.cols,
              rows: term.rows,
            }),
          );
        }
      } catch {}
    };
    window.addEventListener("resize", onResize);
    const ro = new ResizeObserver(onResize);
    ro.observe(containerRef.current);

    return () => {
      window.removeEventListener("resize", onResize);
      ro.disconnect();
      try { ws.close(); } catch {}
      try { term.dispose(); } catch {}
    };
  }, [workspaceId]);

  return (
    <div className="h-full w-full bg-[#0b0b0d]">
      <div ref={containerRef} className="h-full w-full p-2" />
    </div>
  );
}
