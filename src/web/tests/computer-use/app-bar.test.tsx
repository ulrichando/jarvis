import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { CuAppBar } from "@/components/computer-use/app-bar";

const base = {
  connStatus: "connected" as const, sessionId: "9f3a0000c12", supervised: true, takeover: false,
  connected: true, running: false, hasThread: true,
  sessions: [], onLoadSessions: () => {}, onPickSession: () => {},
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
  it("hides the session chip until the client-side id exists (SSR-safe)", () => {
    const { rerender } = render(<CuAppBar {...base} sessionId="" />);
    expect(screen.queryByText(/^session/)).toBeNull();
    rerender(<CuAppBar {...base} sessionId="9f3a0000c12" />);
    expect(screen.getByText(/^session/)).toBeTruthy();
  });
  it("lists sessions in the chip dropdown and picks a non-current one", () => {
    const onPickSession = vi.fn();
    const onLoadSessions = vi.fn();
    const sessions = [
      { id: "9f3a0000c12", status: "idle", task: "current one" },
      { id: "ab120000f99", status: "running", task: "long research job" },
    ];
    render(<CuAppBar {...base} sessions={sessions} onPickSession={onPickSession} onLoadSessions={onLoadSessions} />);
    fireEvent.click(screen.getByTitle("Sessions"));
    expect(onLoadSessions).toHaveBeenCalled();
    fireEvent.click(screen.getByText("long research job"));
    expect(onPickSession).toHaveBeenCalledWith(sessions[1]);
    fireEvent.click(screen.getByTitle("Sessions")); // menu closed on pick — reopen
    fireEvent.click(screen.getByText("current one"));
    expect(onPickSession).toHaveBeenCalledTimes(1); // picking the current session is a no-op
  });
});
