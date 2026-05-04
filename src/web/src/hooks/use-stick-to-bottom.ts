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
  /** True when the user is within STICK_THRESHOLD_PX of the bottom. */
  isAtBottom: boolean;
  /** Smooth-scroll the container all the way down and re-arm stickiness. */
  scrollToBottom: () => void;
};

/**
 * Track whether the scroll container is "near" the bottom and expose
 * a setter to jump back. Pure observation — does NOT auto-scroll on
 * its own; callers gate their auto-scroll on `isAtBottom`.
 *
 * Listens to: scroll (passive), window resize, and ResizeObserver on
 * the container so dynamic content height changes (streaming, image
 * loads) re-evaluate the threshold without manual events.
 */
export function useStickToBottom(
  ref: RefObject<HTMLElement | null>,
): StickToBottom {
  const [isAtBottom, setIsAtBottom] = useState(true);
  // Latest measured distance from bottom — used by scrollToBottom to
  // avoid an extra layout read.
  const lastDistanceRef = useRef(0);

  const measure = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    lastDistanceRef.current = distance;
    setIsAtBottom(distance <= STICK_THRESHOLD_PX);
  }, [ref]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    measure();
    el.addEventListener("scroll", measure, { passive: true });
    window.addEventListener("resize", measure);
    // Re-measure when the content's height changes (a streamed token,
    // a newly-mounted image). Without this the pill stays hidden
    // even after the page outgrows the viewport.
    const ro = new ResizeObserver(() => measure());
    ro.observe(el);
    // Also observe the FIRST child if present — many designs nest
    // the actual content one level deep with `overflow: visible`.
    const inner = el.firstElementChild;
    if (inner) ro.observe(inner);
    return () => {
      el.removeEventListener("scroll", measure);
      window.removeEventListener("resize", measure);
      ro.disconnect();
    };
  }, [ref, measure]);

  const scrollToBottom = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    // Optimistically flip — measure() will confirm on the next scroll
    // event, but flipping early lets the pill animate out immediately.
    setIsAtBottom(true);
  }, [ref]);

  return { isAtBottom, scrollToBottom };
}
