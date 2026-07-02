"use client";

import { useSyncExternalStore } from "react";

// Tailwind's `md` breakpoint. Below this the app shell switches to
// mobile behavior (sidebar becomes an overlay drawer).
const QUERY = "(max-width: 767px)";

function subscribe(cb: () => void) {
  const mql = window.matchMedia(QUERY);
  mql.addEventListener("change", cb);
  return () => mql.removeEventListener("change", cb);
}

/** True below Tailwind's `md` breakpoint. SSR/first paint: false. */
export function useIsMobile(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(QUERY).matches,
    () => false,
  );
}
