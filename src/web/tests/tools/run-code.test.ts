import { afterEach, describe, expect, it, vi } from "vitest";

const dockerStatus = vi.fn();
const execInRuntime = vi.fn();
vi.mock("@/lib/workspace/docker", () => ({
  dockerStatus: (...a: unknown[]) => dockerStatus(...a),
  execInRuntime: (...a: unknown[]) => execInRuntime(...a),
}));

import { createRunCodeTool } from "../../src/lib/tools/run-code";

type Out = {
  status: "ok" | "error";
  language?: string;
  exitCode?: number;
  stdout?: string;
  stderr?: string;
  durationMs?: number;
  error?: string;
};

const tool = createRunCodeTool("user-123");
const run = (code: string, language?: "python" | "bash" | "javascript"): Promise<Out> =>
  (
    tool.execute as (
      i: { code: string; language?: string },
      o: unknown,
    ) => Promise<Out>
  )({ code, language }, {});

afterEach(() => {
  dockerStatus.mockReset();
  execInRuntime.mockReset();
});

describe("createRunCodeTool", () => {
  it("runs python by default in the per-user sandbox and returns output", async () => {
    dockerStatus.mockResolvedValue({ available: true, imageReady: true });
    execInRuntime.mockResolvedValue({
      stdout: "42\n",
      stderr: "",
      exitCode: 0,
      durationMs: 12,
    });
    const out = await run("print(6*7)");
    expect(out.status).toBe("ok");
    expect(out.language).toBe("python");
    expect(out.stdout).toBe("42\n");
    expect(out.exitCode).toBe(0);
    // Runs in the user-scoped sandbox id.
    expect(execInRuntime.mock.calls[0][0]).toBe("ci-user-123");
    // Command carries the base64 of the code + the python interpreter, never
    // the raw code (no shell-escaping exposure).
    const cmd = execInRuntime.mock.calls[0][1] as string;
    expect(cmd).toContain(Buffer.from("print(6*7)", "utf8").toString("base64"));
    expect(cmd).toContain("python3");
    expect(cmd).not.toContain("print(6*7)");
  });

  it("selects the interpreter + extension per language", async () => {
    dockerStatus.mockResolvedValue({ available: true, imageReady: true });
    execInRuntime.mockResolvedValue({ stdout: "", stderr: "", exitCode: 0, durationMs: 1 });
    await run("echo hi", "bash");
    expect(execInRuntime.mock.calls[0][1]).toContain("bash ");
    execInRuntime.mockClear();
    await run("console.log(1)", "javascript");
    const cmd = execInRuntime.mock.calls[0][1] as string;
    expect(cmd).toContain("node ");
    expect(cmd).toContain(".js");
  });

  it("errors clearly when docker is unavailable", async () => {
    dockerStatus.mockResolvedValue({ available: false, imageReady: false });
    const out = await run("print(1)");
    expect(out.status).toBe("error");
    expect(out.error).toMatch(/isn't available/i);
    expect(execInRuntime).not.toHaveBeenCalled();
  });

  it("errors when the sandbox image isn't built", async () => {
    dockerStatus.mockResolvedValue({ available: true, imageReady: false });
    const out = await run("print(1)");
    expect(out.status).toBe("error");
    expect(execInRuntime).not.toHaveBeenCalled();
  });

  it("surfaces a non-zero exit + stderr without throwing", async () => {
    dockerStatus.mockResolvedValue({ available: true, imageReady: true });
    execInRuntime.mockResolvedValue({
      stdout: "",
      stderr: "Traceback: NameError",
      exitCode: 1,
      durationMs: 8,
    });
    const out = await run("undefined_var");
    expect(out.status).toBe("ok"); // ran; the program failed, not the tool
    expect(out.exitCode).toBe(1);
    expect(out.stderr).toMatch(/NameError/);
  });

  it("reports a timeout as an error result (never throws)", async () => {
    dockerStatus.mockResolvedValue({ available: true, imageReady: true });
    execInRuntime.mockRejectedValue(new Error("Command failed: docker exec … timed out"));
    const out = await run("while True: pass");
    expect(out.status).toBe("error");
    expect(out.error).toMatch(/timed out/i);
  });

  it("truncates runaway output", async () => {
    dockerStatus.mockResolvedValue({ available: true, imageReady: true });
    execInRuntime.mockResolvedValue({
      stdout: "x".repeat(20_000),
      stderr: "",
      exitCode: 0,
      durationMs: 5,
    });
    const out = await run("print('x'*20000)");
    expect(out.stdout!.length).toBeLessThan(20_000);
    expect(out.stdout).toMatch(/truncated/);
  });
});
