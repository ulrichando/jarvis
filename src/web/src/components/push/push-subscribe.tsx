"use client";

import { useEffect } from "react";

// VAPID public keys are base64url; the Push API wants a Uint8Array.
function urlBase64ToUint8Array(base64: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const b64 = (base64 + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(b64);
  // Back it with a concrete ArrayBuffer so the type is Uint8Array<ArrayBuffer>
  // (BufferSource) — a plain `new Uint8Array(n)` infers ArrayBufferLike, which
  // the Push API's applicationServerKey type rejects under strict lib.dom.
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

// Registers the service worker and subscribes this browser to Web Push so
// Dispatch can ping the phone when a task finishes / needs a go-ahead. Renders
// nothing. Fully fail-soft: unsupported browser, VAPID unconfigured, or denied
// permission all no-op (degrading to the in-tab Notification the session view
// already shows). iOS: only works after "Add to Home Screen" (installed PWA).
export function PushSubscribe() {
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) return;
    let cancelled = false;
    const run = async () => {
      try {
        const reg = await navigator.serviceWorker.register("/sw.js");
        const res = await fetch("/api/push/vapid-public-key");
        const { enabled, key } = (await res.json()) as {
          enabled?: boolean;
          key?: string | null;
        };
        if (cancelled || !enabled || !key) return;
        if (Notification.permission === "denied") return;
        if (Notification.permission === "default") {
          const perm = await Notification.requestPermission();
          if (perm !== "granted") return;
        }
        const existing = await reg.pushManager.getSubscription();
        const sub =
          existing ??
          (await reg.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: urlBase64ToUint8Array(key),
          }));
        await fetch("/api/push/subscribe", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ subscription: sub.toJSON() }),
        });
      } catch {
        /* fail soft — in-tab notifications still work */
      }
    };
    void run();
    return () => {
      cancelled = true;
    };
  }, []);
  return null;
}
