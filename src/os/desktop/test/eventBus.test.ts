import { test, expect } from "bun:test";
import { EventBus } from "../bridge/events.ts";

test("subscribers receive emitted events", () => {
  const bus = new EventBus();
  const received: unknown[] = [];
  bus.subscribe((e) => received.push(e));
  bus.emit({ type: "voice.mode_changed", mode: "ptt", changedAt: 1 });
  expect(received).toHaveLength(1);
  expect((received[0] as { mode: string }).mode).toBe("ptt");
});

test("unsubscribe stops delivery", () => {
  const bus = new EventBus();
  const seen: string[] = [];
  const unsub = bus.subscribe((e) => seen.push(e.type));
  bus.emit({ type: "voice.wake_triggered", at: 1 });
  unsub();
  bus.emit({ type: "voice.wake_triggered", at: 2 });
  expect(seen).toEqual(["voice.wake_triggered"]);
});

test("size reflects subscriber count", () => {
  const bus = new EventBus();
  expect(bus.size).toBe(0);
  const u1 = bus.subscribe(() => {});
  const u2 = bus.subscribe(() => {});
  expect(bus.size).toBe(2);
  u1();
  expect(bus.size).toBe(1);
  u2();
  expect(bus.size).toBe(0);
});

test("a throwing listener does not prevent other listeners from firing", () => {
  const bus = new EventBus();
  let goodCalled = false;
  bus.subscribe(() => { throw new Error("boom"); });
  bus.subscribe(() => { goodCalled = true; });
  // The console.error in the catch is fine; we just verify the good listener fires.
  bus.emit({ type: "voice.wake_triggered", at: 0 });
  expect(goodCalled).toBe(true);
});

test("a listener that unsubscribes mid-emit does not skip other listeners", () => {
  const bus = new EventBus();
  let bCalled = false;
  const unsubA = bus.subscribe(() => { unsubA(); });
  bus.subscribe(() => { bCalled = true; });
  bus.emit({ type: "voice.wake_triggered", at: 0 });
  expect(bCalled).toBe(true);
});
