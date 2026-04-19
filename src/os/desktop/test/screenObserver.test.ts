import { test, expect } from "bun:test";
import { capture, toBase64 } from "../screen/observer.ts";

function fakeSpawn(stdoutBytes: Uint8Array, exitCode = 0, stderrText = ""): () => Bun.Subprocess {
  return () => ({
    stdout: new ReadableStream({
      start(controller) {
        controller.enqueue(stdoutBytes);
        controller.close();
      },
    }),
    stderr: new ReadableStream({
      start(controller) {
        if (stderrText) controller.enqueue(new TextEncoder().encode(stderrText));
        controller.close();
      },
    }),
    exited: Promise.resolve(exitCode),
    exitCode,
  }) as unknown as Bun.Subprocess;
}

test("capture returns JPEG bytes on successful grim", async () => {
  const fakeJpeg = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0x00, 0x10]);
  const result = await capture({ spawn: fakeSpawn(fakeJpeg) });
  expect(result.jpeg[0]).toBe(0xff);
  expect(result.jpeg[1]).toBe(0xd8);
});

test("capture throws when grim exits non-zero", async () => {
  const spawn = fakeSpawn(new Uint8Array(), 1, "grim: no outputs");
  await expect(capture({ spawn })).rejects.toThrow(/grim failed/);
});

test("toBase64 produces a correct base64 encoding", () => {
  const bytes = new Uint8Array([0x48, 0x65, 0x6c, 0x6c, 0x6f]);
  expect(toBase64(bytes)).toBe("SGVsbG8=");
});
