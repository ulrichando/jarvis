"use client";

import { useEffect } from "react";

type Shortcuts = {
  /** Stop the in-flight stream. Wired to Esc when isStreaming. */
  onStop: () => void;
  /** Open a fresh /chat. Wired to Cmd/Ctrl+Shift+O. */
  onNewChat: () => void;
  /** Toggle the shortcuts-help modal. Wired to Cmd/Ctrl+/. */
  onToggleHelp: () => void;
  /** True only while a stream is running — Esc is gated on this so it
   *  doesn't steal Escape from popovers, modals, etc. */
  isStreaming: boolean;
};

const isMac = () =>
  typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);

/**
 * Global chat keyboard shortcuts. Mirrors ChatGPT/Claude.ai's pack:
 *   - Esc                : stop generation (only while streaming)
 *   - Shift+Esc          : focus the composer textarea
 *   - Cmd/Ctrl+Shift+O   : new chat
 *   - Cmd/Ctrl+/         : toggle keyboard-shortcuts help
 *
 * Skips when the user is editing inside an <input> / <textarea> /
 * contenteditable, EXCEPT for Esc (we always honor stop) and Shift+Esc
 * (which is _meant_ for focus jumping). This matches the de-facto
 * pattern from cmdk/kbar so typing "/" inside the composer doesn't
 * pop the help modal.
 */
export function useKeyboardShortcuts(s: Shortcuts) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const editable =
        !!target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable);

      // Esc — stop streaming. Always wins over editor focus, since
      // the user expects Esc to interrupt regardless of where focus
      // happens to sit. Don't preventDefault when not streaming so
      // the native dismiss-popover behavior still works.
      if (e.key === "Escape" && !e.shiftKey) {
        if (s.isStreaming) {
          e.preventDefault();
          s.onStop();
        }
        return;
      }

      // Shift+Esc — focus composer. Lets the user jump back to type
      // from anywhere (e.g. after scrolling up to read).
      if (e.key === "Escape" && e.shiftKey) {
        const ta = document.querySelector<HTMLTextAreaElement>(
          "textarea[data-jarvis-composer]",
        );
        if (ta) {
          e.preventDefault();
          ta.focus();
          // Place caret at end so they can keep typing.
          const len = ta.value.length;
          ta.setSelectionRange(len, len);
        }
        return;
      }

      // The remaining shortcuts modify global app state — don't fire
      // them while the user is mid-edit in a field.
      if (editable) return;

      const mod = isMac() ? e.metaKey : e.ctrlKey;
      if (!mod) return;

      // Cmd/Ctrl+Shift+O — new chat
      if (e.shiftKey && (e.key === "o" || e.key === "O")) {
        e.preventDefault();
        s.onNewChat();
        return;
      }

      // Cmd/Ctrl+/ — toggle shortcuts help
      if (!e.shiftKey && e.key === "/") {
        e.preventDefault();
        s.onToggleHelp();
        return;
      }
    };

    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [s]);
}
