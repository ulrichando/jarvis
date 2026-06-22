"use client";

import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { computeThumbSize } from "@/lib/computer-use/timeline";

export type NoVNCHandle = { snapshot: (maxW?: number) => string | null };

// @novnc/novnc@1.7 exports the RFB class as the package default (core/rfb.js).
// It touches window/DOM at construction, so we import it dynamically inside the
// effect — never at module top level — to keep it out of SSR.
type RFBLike = {
  scaleViewport: boolean;
  background: string;
  viewOnly: boolean;
  addEventListener: (type: string, cb: (e: { detail?: { clean?: boolean } }) => void) => void;
  disconnect: () => void;
};

type Props = {
  wsUrl: string;
  password: string;
  /** When true the user only watches; false lets their mouse/keyboard drive the
   *  desktop ("take control"). Toggled live without reconnecting. */
  viewOnly?: boolean;
  onState?: (state: "connecting" | "connected" | "disconnected") => void;
  className?: string;
};

export const NoVNCView = forwardRef<NoVNCHandle, Props>(function NoVNCView(
  { wsUrl, password, viewOnly = true, onState, className },
  ref,
) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const rfbRef = useRef<RFBLike | null>(null);
  const viewOnlyRef = useRef(viewOnly);
  const [status, setStatus] = useState<"connecting" | "connected" | "disconnected">("connecting");
  const [error, setError] = useState<string | null>(null);

  // Live takeover toggle — update the existing RFB, don't reconnect.
  useEffect(() => {
    viewOnlyRef.current = viewOnly;
    if (rfbRef.current) rfbRef.current.viewOnly = viewOnly;
  }, [viewOnly]);

  useImperativeHandle(ref, () => ({
    snapshot(maxW = 128) {
      try {
        const canvas = containerRef.current?.querySelector("canvas");
        if (!canvas) return null;
        const { w, h } = computeThumbSize(canvas.width, canvas.height, maxW);
        if (!w || !h) return null;
        const off = document.createElement("canvas");
        off.width = w;
        off.height = h;
        const ctx = off.getContext("2d");
        if (!ctx) return null;
        ctx.drawImage(canvas, 0, 0, w, h);
        return off.toDataURL("image/jpeg", 0.5);
      } catch {
        return null;
      }
    },
  }), []);

  useEffect(() => {
    let rfb: RFBLike | null = null;
    let cancelled = false;
    if (!containerRef.current || !wsUrl) return;

    setStatus("connecting");
    setError(null);
    onState?.("connecting");

    (async () => {
      try {
        const { default: RFB } = (await import("@novnc/novnc")) as {
          default: new (
            target: HTMLElement,
            url: string,
            opts?: { credentials?: { password?: string } },
          ) => RFBLike;
        };
        if (cancelled || !containerRef.current) return;
        rfb = new RFB(containerRef.current, wsUrl, { credentials: { password } });
        rfb.scaleViewport = true; // fit the canvas to the container
        rfb.background = "#0a0a0a";
        rfb.viewOnly = viewOnlyRef.current;
        rfbRef.current = rfb;
        rfb.addEventListener("connect", () => {
          if (cancelled || !rfb) return;
          rfb.viewOnly = viewOnlyRef.current; // some builds reset this on connect
          setStatus("connected");
          onState?.("connected");
        });
        rfb.addEventListener("disconnect", (e) => {
          if (cancelled) return;
          setStatus("disconnected");
          onState?.("disconnected");
          if (e?.detail && e.detail.clean === false) setError("Disconnected from the desktop stream.");
        });
      } catch (err) {
        if (cancelled) return;
        setStatus("disconnected");
        onState?.("disconnected");
        setError(err instanceof Error ? err.message : "Failed to load the VNC client.");
      }
    })();

    return () => {
      cancelled = true;
      try {
        rfb?.disconnect();
      } catch {
        /* already gone */
      }
      rfbRef.current = null;
    };
  }, [wsUrl, password, onState]);

  return (
    <div className={className} style={{ position: "relative" }}>
      <div ref={containerRef} style={{ width: "100%", height: "100%" }} />
      {status !== "connected" && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center text-xs text-neutral-500">
          {error ?? (status === "connecting" ? "Connecting to desktop…" : "Desktop disconnected")}
        </div>
      )}
    </div>
  );
});
