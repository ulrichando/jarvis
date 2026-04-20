// Tests for client/audio.ts with mocked spawner (no real pulseaudio dependency).

import { test, expect } from "bun:test";
import { startRecording, playAudio } from "../client/audio.ts";

function fakeSpawn(stdoutBytes: Uint8Array, exitCode = 0): (cmd: string[]) => Bun.Subprocess {
  return (cmd: string[]) => {
    void cmd;
    return {
      stdout: new ReadableStream({ start(c) { c.enqueue(stdoutBytes); c.close(); } }),
      stderr: new ReadableStream({ start(c) { c.close(); } }),
      exited: Promise.resolve(exitCode),
      exitCode,
      kill: () => { /* noop */ },
    } as unknown as Bun.Subprocess;
  };
}

test("startRecording returns a handle whose done resolves with captured bytes", async () => {
  const bytes = new Uint8Array([0x52, 0x49, 0x46, 0x46, 0x01, 0x02]);
  const rec = startRecording({ spawn: fakeSpawn(bytes), maxSeconds: 5 });
  expect(typeof rec.stop).toBe("function");
  const captured = await rec.done;
  expect(captured).toEqual(bytes);
});

test("startRecording.stop is idempotent", async () => {
  const rec = startRecording({ spawn: fakeSpawn(new Uint8Array()), maxSeconds: 1 });
  rec.stop();
  rec.stop();
  rec.stop();
  await rec.done; // shouldn't throw
});

test("playAudio invokes paplay with stdin piping and resolves on exit 0", async () => {
  let spawnedCmd: string[] | undefined;
  let pipedBytes: Uint8Array | undefined;
  const spawn = (cmd: string[], input: Uint8Array): Bun.Subprocess => {
    spawnedCmd = cmd;
    pipedBytes = input;
    return {
      exited: Promise.resolve(0),
      exitCode: 0,
    } as unknown as Bun.Subprocess;
  };
  const bytes = new Uint8Array([0x10, 0x20]);
  await playAudio(bytes, { spawn });
  expect(spawnedCmd?.[0]).toBe("paplay");
  expect(pipedBytes).toEqual(bytes);
});

test("playAudio throws on non-zero exit", async () => {
  const spawn = (): Bun.Subprocess => ({
    exited: Promise.resolve(2),
    exitCode: 2,
  } as unknown as Bun.Subprocess);
  await expect(playAudio(new Uint8Array([0x00]), { spawn })).rejects.toThrow(/paplay exited with code 2/);
});
