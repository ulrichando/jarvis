"use client";

import { useEffect, useRef } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

type Props = {
  workspaceId: string;
};

// xterm.js connects to the standalone PTY sidecar (scripts/pty-server.mjs),
// which is started alongside Next by `npm run dev` (concurrently → pty).
// Default URL ws://<host>:8772/pty; override via NEXT_PUBLIC_PTY_URL.

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
    termRef.current = term;
    fitRef.current = fit;

    // Defer the first fit to the next frame: fitting synchronously right
    // after open() can size the terminal to a 0×0 box (layout hasn't run
    // yet) and leave it tiny until the first resize event.
    const rafId = requestAnimationFrame(() => {
      try {
        fit.fit();
      } catch {
        /* container not measurable yet — the ResizeObserver will retry */
      }
    });

    const wsUrl =
      process.env.NEXT_PUBLIC_PTY_URL ??
      `ws://${window.location.hostname}:8772/pty`;

    let disposed = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    let everOpened = false;
    let hintShown = false;
    let ws: WebSocket | null = null;

    // Per-session credential for the PTY socket. Same-origin POST → the cookie
    // carries the app session, which the /api/* gate authenticates. Best-effort:
    // a loopback/dev sidecar doesn't require it, so a fetch failure still lets
    // the terminal connect (the sidecar is the one that enforces). Re-minted on
    // every reconnect so the token TTL can stay short.
    const fetchPtyToken = async (): Promise<string | undefined> => {
      try {
        const res = await fetch(
          `/api/workspace/${encodeURIComponent(workspaceId)}/pty-token`,
          { method: "POST" },
        );
        if (!res.ok) return undefined;
        const j = await res.json();
        return typeof j?.token === "string" ? j.token : undefined;
      } catch {
        return undefined;
      }
    };

    const connect = async () => {
      if (disposed) return;
      if (attempt === 0) {
        term.write("\r\n\x1b[2m[connecting to terminal…]\x1b[0m\r\n");
      }
      const token = await fetchPtyToken();
      if (disposed) return; // unmounted while awaiting the token
      ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        everOpened = true;
        attempt = 0; // reset backoff after a good connection
        hintShown = false;
        ws!.send(
          JSON.stringify({
            type: "init",
            workspaceId,
            token,
            cols: term.cols,
            rows: term.rows,
          }),
        );
      };
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === "output") term.write(msg.data);
          else if (msg.type === "exit")
            term.write(`\r\n[exited code=${msg.code}]\r\n`);
        } catch (err) {
          if (process.env.NODE_ENV !== "production")
            console.warn("[terminal] unparseable PTY frame:", err);
        }
      };
      ws.onerror = () => {
        // Don't assert a cause — the sidecar may be down, restarting, or
        // briefly unreachable. Show the hint once; reconnect handles the
        // transient cases.
        if (!everOpened && !hintShown) {
          hintShown = true;
          term.write(
            "\r\n\x1b[31m[can't reach the terminal sidecar on :8772 — it starts with `npm run dev`]\x1b[0m\r\n",
          );
        }
      };
      ws.onclose = () => {
        if (disposed) return;
        if (everOpened)
          term.write("\r\n\x1b[2m[disconnected — reconnecting…]\x1b[0m\r\n");
        // Exponential backoff (capped at 5s) so a sidecar restart or a
        // network blip self-heals instead of leaving a dead terminal.
        const delay = Math.min(500 * 2 ** attempt, 5000);
        attempt += 1;
        reconnectTimer = setTimeout(connect, delay);
      };
    };

    connect();

    term.onData((data) => {
      if (ws && ws.readyState === ws.OPEN) {
        ws.send(JSON.stringify({ type: "input", data }));
      }
    });

    const onResize = () => {
      try {
        fit.fit();
        if (ws && ws.readyState === ws.OPEN) {
          ws.send(
            JSON.stringify({
              type: "resize",
              cols: term.cols,
              rows: term.rows,
            }),
          );
        }
      } catch {
        /* ignore transient measure failures */
      }
    };
    window.addEventListener("resize", onResize);
    const ro = new ResizeObserver(onResize);
    ro.observe(containerRef.current);

    return () => {
      disposed = true;
      cancelAnimationFrame(rafId);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      window.removeEventListener("resize", onResize);
      ro.disconnect();
      try {
        ws?.close();
      } catch {
        /* already closing */
      }
      try {
        term.dispose();
      } catch {
        /* already disposed */
      }
    };
  }, [workspaceId]);

  return (
    <div className="h-full w-full bg-[#0b0b0d]">
      <div ref={containerRef} className="h-full w-full p-2" />
    </div>
  );
}
