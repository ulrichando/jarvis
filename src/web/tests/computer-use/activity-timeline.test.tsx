import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ActivityTimeline } from "@/components/computer-use/activity-timeline";
import type { ChatMsg } from "@/lib/computer-use/timeline";

const thread: ChatMsg[] = [
  { role: "user", parts: [{ kind: "text", text: "Open Firefox" }] },
  { role: "assistant", parts: [
    { kind: "text", text: "I'll launch Firefox." },
    { kind: "action", text: "Clicked Firefox", ts: new Date(2026, 0, 1, 14, 32, 2).getTime() },
  ] },
];

describe("ActivityTimeline", () => {
  it("renders the task, reasoning, and a timestamped step", () => {
    render(<ActivityTimeline thread={thread} running={false} runStart={null} ready onApprove={() => {}} onRunExample={() => {}} />);
    expect(screen.getByText("Open Firefox")).toBeTruthy();
    expect(screen.getByText("I'll launch Firefox.")).toBeTruthy();
    expect(screen.getByText("Clicked Firefox")).toBeTruthy();
    expect(screen.getByText("14:32:02")).toBeTruthy();
  });
  it("shows examples and runs one when empty + ready", () => {
    const onRunExample = vi.fn();
    render(<ActivityTimeline thread={[]} running={false} runStart={null} ready onApprove={() => {}} onRunExample={onRunExample} />);
    fireEvent.click(screen.getByText("Take a screenshot and tell me what's open"));
    expect(onRunExample).toHaveBeenCalledWith("Take a screenshot and tell me what's open");
  });
});
