import { describe, it, expect } from "vitest";
import { createRef } from "react";
import { render, screen } from "@testing-library/react";
import { DesktopStage } from "@/components/computer-use/desktop-stage";
import type { NoVNCHandle } from "@/components/computer-use/novnc-view";

const base = {
  novncRef: createRef<NoVNCHandle>(),
  takeover: false, running: false,
  onTakeControl: () => {}, onGiveControl: () => {}, onConnect: () => {}, onRecheck: () => {}, onVncState: () => {},
};

describe("DesktopStage", () => {
  it("shows the services checklist when not ready", () => {
    render(<DesktopStage {...base} status={{ ready: false, streamUp: false, sidecarUp: true, wsUrl: "", password: null, hint: "run the stream" }} connected />);
    expect(screen.getByText(/Desktop stream not ready/)).toBeTruthy();
    expect(screen.getByText(/computer-use sidecar/)).toBeTruthy();
  });
  it("shows a reconnect card when disconnected", () => {
    render(<DesktopStage {...base} status={{ ready: true, streamUp: true, sidecarUp: true, wsUrl: "ws://x", password: "p", hint: null }} connected={false} />);
    expect(screen.getByText(/Disconnected from the desktop/)).toBeTruthy();
  });
});
