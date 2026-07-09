import { describe, it, expect } from "vitest";

// Characterization tests for finding #13b — the pure helpers the chat.tsx
// stream loop depends on for stage progression, truncation recovery, and the
// synthetic result/verify blocks it feeds back to the model on the next turn.
//
// These were already module-level pure functions inside chat.tsx; they are now
// exported so the SSE-loop behavior they drive can be pinned before the
// useChatStream / useActionRunner extraction. Nothing here changes behavior —
// the tests assert the CURRENT output so an extraction that alters it fails
// loudly.

import {
  detectStageCount,
  hasOpenArtifactTag,
  renderStageProgressPrompt,
  renderActionResultsBlock,
  clipOutput,
  renderVerifyBlock,
  isDiagnosticCommand,
  type CapturedResultLite,
} from "@/components/chat/chat";
import type { VerifyOutcome } from "@/lib/verify/types";

describe("detectStageCount — multi-stage plan detection", () => {
  it("returns N for a valid `<jarvisplan stages=\"N\">` in range 2..5", () => {
    expect(detectStageCount('<jarvisplan stages="2">…</jarvisplan>')).toBe(2);
    expect(detectStageCount('<jarvisPlan stages="5">')).toBe(5);
  });

  it("is case-insensitive on the tag and accepts single quotes", () => {
    expect(detectStageCount("<JARVISPLAN STAGES='3'>")).toBe(3);
  });

  it("rejects out-of-range counts (< 2 or > 5)", () => {
    expect(detectStageCount('<jarvisplan stages="1">')).toBeNull();
    expect(detectStageCount('<jarvisplan stages="6">')).toBeNull();
    expect(detectStageCount('<jarvisplan stages="0">')).toBeNull();
  });

  it("returns null when there's no plan tag", () => {
    expect(detectStageCount("just some prose, no plan here")).toBeNull();
    expect(detectStageCount("")).toBeNull();
  });

  it("finds the plan tag even when embedded mid-text", () => {
    expect(
      detectStageCount('Here is my plan.\n<jarvisplan stages="4">a</jarvisplan>'),
    ).toBe(4);
  });
});

describe("hasOpenArtifactTag — truncation detection", () => {
  it("false when boltArtifact/boltAction are balanced", () => {
    expect(
      hasOpenArtifactTag("<boltArtifact><boltAction>x</boltAction></boltArtifact>"),
    ).toBe(false);
  });

  it("true when a boltArtifact opens without closing (stream cut mid-tag)", () => {
    expect(hasOpenArtifactTag('<boltArtifact id="x"> partial content')).toBe(
      true,
    );
  });

  it("true when a boltAction opens without closing", () => {
    expect(hasOpenArtifactTag('<boltAction type="file"> half a file')).toBe(
      true,
    );
  });

  it("is case-insensitive", () => {
    expect(hasOpenArtifactTag("<BOLTARTIFACT>")).toBe(true);
  });

  it("false for text with no bolt tags at all", () => {
    expect(hasOpenArtifactTag("plain assistant reply")).toBe(false);
    expect(hasOpenArtifactTag("")).toBe(false);
  });

  it("counts opens vs closes — extra close doesn't read as open", () => {
    // more closes than opens → not open
    expect(
      hasOpenArtifactTag("<boltAction>a</boltAction></boltAction>"),
    ).toBe(false);
  });
});

describe("renderStageProgressPrompt — auto-progression continuation prompt", () => {
  it("names the current+next stage and restates the goal rule", () => {
    const p = renderStageProgressPrompt(3, 2);
    expect(p).toContain("[auto-progress to stage 2 of 3]");
    expect(p).toContain("Stage 1 verified green");
    expect(p).toContain("implement stage 2 from your earlier plan");
    expect(p).toContain("Re-state the goal of stage 2");
  });

  it("on a NON-final stage, promises the runtime auto-fires the next one", () => {
    const p = renderStageProgressPrompt(3, 2);
    expect(p).toContain("auto-fire stage 3 after this verifies");
    expect(p).not.toContain("This is the FINAL stage");
  });

  it("on the FINAL stage, says it's final and forbids a new plan tag", () => {
    const p = renderStageProgressPrompt(3, 3);
    expect(p).toContain("This is the FINAL stage");
    expect(p).toContain('Don\'t emit a "<jarvisPlan stages=...">" tag');
    expect(p).not.toContain("auto-fire");
  });
});

describe("renderActionResultsBlock — feed shell output back to the model", () => {
  const base: CapturedResultLite = {
    actionId: "a1",
    type: "shell",
    command: "npm test",
    exitCode: 0,
    stdout: "3 passing",
    stderr: "",
    detached: false,
  };

  it("wraps results in a <boltActionResults> block with per-result meta", () => {
    const out = renderActionResultsBlock([base]);
    expect(out).toContain("<boltActionResults>");
    expect(out).toContain('<result actionId="a1" type="shell" exitCode="0">');
    expect(out).toContain("<command>npm test</command>");
    expect(out).toContain("<stdout>");
    expect(out).toContain("3 passing");
    expect(out).toContain("</boltActionResults>");
  });

  it("emits a background note for detached actions (no output captured)", () => {
    const out = renderActionResultsBlock([{ ...base, detached: true }]);
    expect(out).toContain("started in background; output not captured");
    expect(out).not.toContain("<stdout>");
  });

  it("emits `(no output)` when both stdout and stderr are empty", () => {
    const out = renderActionResultsBlock([
      { ...base, stdout: "", stderr: "" },
    ]);
    expect(out).toContain("(no output)");
  });

  it("includes a <stderr> block when stderr is present", () => {
    const out = renderActionResultsBlock([
      { ...base, exitCode: 1, stderr: "boom" },
    ]);
    expect(out).toContain('exitCode="1"');
    expect(out).toContain("<stderr>");
    expect(out).toContain("boom");
  });

  it("renders each result for a multi-action turn", () => {
    const out = renderActionResultsBlock([
      base,
      { ...base, actionId: "a2", command: "tsc" },
    ]);
    expect(out).toContain('actionId="a1"');
    expect(out).toContain('actionId="a2"');
    expect(out).toContain("<command>tsc</command>");
  });
});

describe("clipOutput — context-budget guard for large blobs", () => {
  it("returns short strings unchanged", () => {
    expect(clipOutput("small")).toBe("small");
    expect(clipOutput("")).toBe("");
  });

  it("clips oversized strings keeping head + tail with a marker", () => {
    const big = "H".repeat(5000) + "T".repeat(5000); // 10k > 6k default
    const out = clipOutput(big);
    expect(out.length).toBeLessThan(big.length);
    expect(out).toContain("[clipped");
    expect(out).toContain("bytes]");
    // keeps the head (failing line / stack usually there)
    expect(out.startsWith("H")).toBe(true);
    // and the tail
    expect(out.trimEnd().endsWith("T")).toBe(true);
  });

  it("respects a custom maxBytes", () => {
    const s = "x".repeat(100);
    expect(clipOutput(s, 1000)).toBe(s); // under budget
    expect(clipOutput(s, 20)).toContain("[clipped"); // over budget
  });
});

describe("renderVerifyBlock — synthetic verify summary", () => {
  const outcome = (over: Partial<VerifyOutcome> = {}): VerifyOutcome =>
    ({
      ok: true,
      durationMs: 1234,
      fixers: [],
      typecheck: { ran: false, ok: true, output: "" },
      preview: { ran: false, ok: true, status: null },
      screenshot: undefined,
      ...over,
    }) as VerifyOutcome;

  it("emits a <jarvisVerify> wrapper with ok + duration attrs", () => {
    const out = renderVerifyBlock(outcome());
    expect(out).toContain('<jarvisVerify ok="true" durationMs="1234">');
    expect(out).toContain("</jarvisVerify>");
  });

  it("includes a typecheck line when typecheck ran", () => {
    const out = renderVerifyBlock(
      outcome({ typecheck: { ran: true, ok: false, output: "TS2345 boom" } }),
    );
    expect(out).toContain('<typecheck ok="false">TS2345 boom</typecheck>');
  });

  it("includes an autoFixes section listing applied fixers", () => {
    const out = renderVerifyBlock(
      outcome({
        fixers: [
          {
            rule: "prettier",
            filesChanged: ["a.ts"],
            description: "formatted",
          },
        ] as VerifyOutcome["fixers"],
      }),
    );
    expect(out).toContain("<autoFixes>");
    expect(out).toContain('<fix rule="prettier" files="1">formatted</fix>');
  });

  it("records a screenshot marker WITHOUT dumping the data URL", () => {
    const out = renderVerifyBlock(
      outcome({
        screenshot: {
          bytes: 999,
          target: "http://localhost:5173",
          dataUrl: "data:image/png;base64,AAAA",
        } as VerifyOutcome["screenshot"],
      }),
    );
    expect(out).toContain('<screenshot bytes="999" target="http://localhost:5173"/>');
    // The heavy data URL must NEVER land in the text body.
    expect(out).not.toContain("base64");
  });

  it("emits (no output) when typecheck ran with empty output", () => {
    const out = renderVerifyBlock(
      outcome({ typecheck: { ran: true, ok: true, output: "   " } }),
    );
    expect(out).toContain("(no output)");
  });
});

describe("isDiagnosticCommand — read-only command classifier", () => {
  it("recognizes read-only probes that may legitimately exit non-zero", () => {
    expect(isDiagnosticCommand("grep -r foo src")).toBe(true);
    expect(isDiagnosticCommand("rg pattern")).toBe(true);
    expect(isDiagnosticCommand("find . -name x")).toBe(true);
    expect(isDiagnosticCommand("cat file")).toBe(true);
    expect(isDiagnosticCommand("ls -la")).toBe(true);
  });

  it("strips leading `cd … &&` before classifying the real binary", () => {
    expect(isDiagnosticCommand("cd src && grep foo bar")).toBe(true);
    expect(isDiagnosticCommand("cd a && cd b && ls")).toBe(true);
  });

  it("strips leading env-var assignments", () => {
    expect(isDiagnosticCommand("PORT=5173 HOST=0.0.0.0 cat x")).toBe(true);
  });

  it("returns false for mutating / build commands", () => {
    expect(isDiagnosticCommand("npm run build")).toBe(false);
    expect(isDiagnosticCommand("rm -rf dist")).toBe(false);
    expect(isDiagnosticCommand("tsc --noEmit")).toBe(false);
  });

  it("classifies on the first binary of a pipeline", () => {
    expect(isDiagnosticCommand("cat log | grep err")).toBe(true);
    expect(isDiagnosticCommand("npm test | tail")).toBe(false);
  });
});
