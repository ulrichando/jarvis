import { describe, it, expect } from "vitest";
import type { KeyboardEvent } from "react";
import { shouldSubmitOnEnter } from "@/lib/chat/enter-submit";

// Minimal stand-in for the fields shouldSubmitOnEnter reads off a React
// keyboard event. Cast through unknown so we don't have to fabricate the
// full synthetic-event surface.
type Opts = {
  key?: string;
  shiftKey?: boolean;
  isComposing?: boolean;
  keyCode?: number;
};
function ev({
  key = "Enter",
  shiftKey = false,
  isComposing = false,
  keyCode = 13,
}: Opts = {}): KeyboardEvent<HTMLTextAreaElement> {
  return {
    key,
    shiftKey,
    keyCode,
    nativeEvent: { isComposing },
  } as unknown as KeyboardEvent<HTMLTextAreaElement>;
}

describe("shouldSubmitOnEnter (IME-safe Enter-to-send)", () => {
  it("submits on a plain Enter", () => {
    expect(shouldSubmitOnEnter(ev())).toBe(true);
  });

  it("does NOT submit on Shift+Enter (intentional newline)", () => {
    expect(shouldSubmitOnEnter(ev({ shiftKey: true }))).toBe(false);
  });

  it("does NOT submit while an IME is composing (isComposing)", () => {
    // The bug this guards: pressing Enter to confirm a CJK candidate or a
    // dead-key accent would otherwise fire a half-written message.
    expect(shouldSubmitOnEnter(ev({ isComposing: true }))).toBe(false);
  });

  it("does NOT submit on the legacy composing sentinel (keyCode 229)", () => {
    expect(shouldSubmitOnEnter(ev({ keyCode: 229 }))).toBe(false);
  });

  it("ignores non-Enter keys", () => {
    expect(shouldSubmitOnEnter(ev({ key: "a", keyCode: 65 }))).toBe(false);
  });
});
