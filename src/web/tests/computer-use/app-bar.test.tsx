import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { CuAppBar } from "@/components/computer-use/app-bar";

const base = {
  connStatus: "connected" as const, sessionId: "9f3a0000c12", supervised: true, takeover: false,
  connected: true, running: false, hasThread: true,
  onToggleMode: () => {}, onToggleTakeover: () => {}, onToggleConnected: () => {}, onNewChat: () => {}, onStop: () => {}, onRefresh: () => {},
};

describe("CuAppBar", () => {
  it("toggles mode", () => {
    const onToggleMode = vi.fn();
    render(<CuAppBar {...base} onToggleMode={onToggleMode} />);
    fireEvent.click(screen.getByRole("radio", { name: /Auto/ }));
    expect(onToggleMode).toHaveBeenCalled();
  });
  it("shows Stop while running", () => {
    render(<CuAppBar {...base} running />);
    expect(screen.getByTitle("Stop the agent")).toBeTruthy();
  });
});
