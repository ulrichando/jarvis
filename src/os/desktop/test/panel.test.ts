import { test, expect } from "bun:test";
import { PanelState } from "../panels/state.ts";
import { EventBus } from "../bridge/events.ts";
import { createPanelTool } from "../agent/tools/panel.ts";

test("PanelState.open assigns id, defaults, and cascades position", () => {
  const s = new PanelState();
  const a = s.open({ kind: "browser", src: "https://example.com" });
  const b = s.open({ kind: "browser", src: "https://example.org" });
  expect(a.id).not.toBe(b.id);
  expect(a.kind).toBe("browser");
  expect(a.width).toBe(560);
  expect(a.height).toBe(420);
  expect(b.x!).toBeGreaterThan(a.x!);
  expect(b.y!).toBeGreaterThan(a.y!);
});

test("PanelState.close removes by id and returns boolean", () => {
  const s = new PanelState();
  const p = s.open({ kind: "text", content: "hi" });
  expect(s.close(p.id)).toBe(true);
  expect(s.close(p.id)).toBe(false);
  expect(s.list()).toHaveLength(0);
});

test("PanelState.update patches in place", () => {
  const s = new PanelState();
  const p = s.open({ kind: "text", content: "hi" });
  const next = s.update(p.id, { title: "renamed" });
  expect(next?.title).toBe("renamed");
  expect(s.get(p.id)?.title).toBe("renamed");
});

test("video panels get wider default", () => {
  const s = new PanelState();
  const v = s.open({ kind: "video", src: "movie.mp4" });
  expect(v.width).toBe(720);
});

test("panel tool open emits panel.opened and returns id", async () => {
  const s = new PanelState();
  const bus = new EventBus();
  const seen: unknown[] = [];
  bus.subscribe((e) => seen.push(e));
  const tool = createPanelTool(s, bus);
  const res = await tool.run({ action: "open", kind: "browser", src: "https://x.com" });
  expect(res.is_error).toBeFalsy();
  expect(seen).toHaveLength(1);
  expect((seen[0] as { type: string }).type).toBe("panel.opened");
  expect(s.list()).toHaveLength(1);
});

test("panel tool open with kind=text requires content", async () => {
  const res = await createPanelTool(new PanelState(), new EventBus()).run({ action: "open", kind: "text" });
  expect(res.is_error).toBe(true);
});

test("panel tool open with non-text kind requires src", async () => {
  const res = await createPanelTool(new PanelState(), new EventBus()).run({ action: "open", kind: "browser" });
  expect(res.is_error).toBe(true);
});

test("panel tool close removes the panel and emits panel.closed", async () => {
  const s = new PanelState();
  const bus = new EventBus();
  const tool = createPanelTool(s, bus);
  await tool.run({ action: "open", kind: "text", content: "hi" });
  const id = s.list()[0]!.id;

  const events: unknown[] = [];
  bus.subscribe((e) => events.push(e));
  const res = await tool.run({ action: "close", id });
  expect(res.is_error).toBeFalsy();
  expect(s.list()).toHaveLength(0);
  expect((events[0] as { type: string }).type).toBe("panel.closed");
});

test("panel tool close with unknown id errors", async () => {
  const res = await createPanelTool(new PanelState(), new EventBus()).run({ action: "close", id: "does-not-exist" });
  expect(res.is_error).toBe(true);
});

test("panel tool clear closes all panels and emits one event per", async () => {
  const s = new PanelState();
  const bus = new EventBus();
  const tool = createPanelTool(s, bus);
  await tool.run({ action: "open", kind: "text", content: "a" });
  await tool.run({ action: "open", kind: "text", content: "b" });

  const closed: string[] = [];
  bus.subscribe((e) => { if ((e as { type: string }).type === "panel.closed") closed.push("x"); });
  const res = await tool.run({ action: "clear" });
  expect(res.is_error).toBeFalsy();
  expect(closed).toHaveLength(2);
  expect(s.list()).toHaveLength(0);
});

test("panel tool list returns a JSON string of panels", async () => {
  const s = new PanelState();
  const bus = new EventBus();
  const tool = createPanelTool(s, bus);
  await tool.run({ action: "open", kind: "text", content: "a" });
  const res = await tool.run({ action: "list" });
  expect(res.is_error).toBeFalsy();
  const parsed = JSON.parse(res.output);
  expect(Array.isArray(parsed)).toBe(true);
  expect(parsed).toHaveLength(1);
});
