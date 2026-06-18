"use client";

import { useEffect, type KeyboardEvent, type RefObject } from "react";

/**
 * IME-safe decision for "should this Enter keystroke submit the message?".
 *
 * Returns true only for a plain Enter (no Shift) that is NOT part of an
 * in-progress IME composition. Without the composition guard, pressing
 * Enter to confirm a CJK candidate — or a dead-key accent on Latin
 * keyboards (´ + e → é) — submits a half-written message. MDN documents
 * `KeyboardEvent.isComposing`; `keyCode === 229` is the legacy "still
 * composing" sentinel some browsers report on keydown instead of setting
 * `isComposing` on the synthetic event, so we check both.
 *
 * The caller still decides whether there's content to send and is
 * responsible for calling `preventDefault()` when this returns true.
 */
export function shouldSubmitOnEnter(
  e: KeyboardEvent<HTMLTextAreaElement>,
): boolean {
  if (e.key !== "Enter" || e.shiftKey) return false;
  // React's synthetic event surfaces composition state on nativeEvent;
  // keyCode 229 covers browsers that don't propagate isComposing here.
  if (e.nativeEvent.isComposing || e.keyCode === 229) return false;
  return true;
}

/**
 * Auto-grow a textarea to fit its content, capped at `max` px — past the
 * cap the textarea scrolls internally instead of pushing surrounding
 * chrome (send button, toolbar) off-screen. Re-runs whenever `value`
 * changes.
 *
 * Both composers inlined this; the code composer's copy forgot the cap,
 * so a long paste grew the box unbounded. Sharing the hook keeps the two
 * from drifting again.
 */
export function useAutoResize(
  ref: RefObject<HTMLTextAreaElement | null>,
  value: string,
  max = 240,
): void {
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, max)}px`;
  }, [ref, value, max]);
}
