import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { CommandBar } from "@/components/computer-use/command-bar";

const base = { value: "", onChange: () => {}, onSubmit: vi.fn(), running: false, disabled: false, model: "claude-sonnet-4-6", setModel: () => {}, placeholder: "Tell Jarvis…" };

describe("CommandBar", () => {
  it("submits on Enter, not Shift+Enter", () => {
    const onSubmit = vi.fn();
    render(<CommandBar {...base} value="do it" onSubmit={onSubmit} />);
    const ta = screen.getByPlaceholderText("Tell Jarvis…");
    fireEvent.keyDown(ta, { key: "Enter", shiftKey: true });
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onSubmit).toHaveBeenCalled();
  });
});
