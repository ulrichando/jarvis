"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type RefObject,
} from "react";

// Threshold in pixels. While the user is within this distance from
// the scroll-bottom, the thread is "stuck" and new messages auto-scroll
// into view. Past this distance the user has explicitly chosen to
// look back at history; we stop forcing scroll and reveal the
// "Jump to latest" pill instead. 70px matches the de-facto standard
// from stackblitz-labs/use-stick-to-bottom (and is what Claude.ai +
// ChatGPT both feel like in practice).
const STICK_THRESHOLD_PX = 70;

export type StickToBottom = {
  /** True when the thread is pinned to the bottom (auto-following). */
  isAtBottom: boolean;
  /** Smooth-scroll the container all the way down and re-arm stickiness. */
  scrollToBottom: () => void;
};

/**
 * Track whether the scroll container is pinned to the bottom and expose a
 * setter to jump back. Pure observation — does NOT auto-scroll on its own;
 * the thread gates its own auto-scroll on `isAtBottom`.
 *
 * Stickiness is a LATCH, not a per-frame distance read:
 *   - DISARM only on a deliberate user scroll UP (scrollTop shrinks).
 *   - RE-ARM whenever the container lands within the threshold of the bottom.
 *   - Content growth (streamed tokens, image loads, expanding reasoning) must
 *     NEVER disarm. The earlier version recomputed `isAtBottom` from raw
 *     distance on every ResizeObserver tick, so a streamed chunk taller than
 *     the threshold flipped it false BEFORE the thread's follow-scroll ran —
 *     which then gated the follow out and froze the view mid-stream (the
 *     "stops scrolling as it generates" bug). Pinning the latch to user
 *     scroll direction fixes that: while pinned, growth keeps us pinned.
 *
 * Listens to: scroll (passive, direction-aware), window resize, and a
 * ResizeObserver on the container so dynamic height changes can RE-ARM
 * (never disarm) the latch.
 */
export function useStickToBottom(
  ref: RefObject<HTMLElement | null>,
): StickToBottom {
  const [isAtBottom, setIsAtBottom] = useState(true);
  // Ref mirror so the event handlers read the live latch without re-binding.
  const isAtBottomRef = useRef(true);
  // Latest measured distance from bottom — used by callers / debugging.
  const lastDistanceRef = useRef(0);
  // Previous scrollTop, to detect scroll DIRECTION (up = user pulling away).
  const lastScrollTopRef = useRef(0);

  const setStuck = useCallback((v: boolean) => {
    isAtBottomRef.current = v;
    setIsAtBottom(v);
  }, []);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const distanceNow = () => {
      const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
      lastDistanceRef.current = distance;
      return distance;
    };

    // Seed the latch + direction baseline from the current position.
    setStuck(distanceNow() <= STICK_THRESHOLD_PX);
    lastScrollTopRef.current = el.scrollTop;

    // Scroll events come from BOTH the user and our own programmatic
    // scroll-to-bottom. Re-arm when we land near the bottom (covers the
    // programmatic case + the user scrolling back down); disarm ONLY when the
    // user scrolls up while away from the bottom.
    const onScroll = () => {
      const distance = distanceNow();
      const prevTop = lastScrollTopRef.current;
      const top = el.scrollTop;
      lastScrollTopRef.current = top;
      if (distance <= STICK_THRESHOLD_PX) {
        if (!isAtBottomRef.current) setStuck(true);
        return;
      }
      // Away from bottom: a genuine upward scroll (top shrank past a small
      // jitter epsilon) means the user wants to read history → unpin.
      if (top < prevTop - 2 && isAtBottomRef.current) setStuck(false);
    };

    // Height changes only ever RE-ARM. While pinned, growth keeps us pinned
    // (the thread's effect does the actual follow-scroll); we must not flip
    // the latch false here or we'd freeze the stream.
    const onResize = () => {
      if (distanceNow() <= STICK_THRESHOLD_PX && !isAtBottomRef.current) {
        setStuck(true);
      }
    };

    el.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onResize);
    // Re-measure when the content's height changes (a streamed token, a
    // newly-mounted image, reasoning expanding).
    const ro = new ResizeObserver(onResize);
    ro.observe(el);
    // Also observe the FIRST child — many designs nest the actual content one
    // level deep with `overflow: visible`.
    const inner = el.firstElementChild;
    if (inner) ro.observe(inner);
    return () => {
      el.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onResize);
      ro.disconnect();
    };
  }, [ref, setStuck]);

  const scrollToBottom = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    // Optimistically re-arm — the scroll handler confirms on the way down,
    // but flipping early lets the pill animate out immediately.
    setStuck(true);
  }, [setStuck]);

  return { isAtBottom, scrollToBottom };
}
