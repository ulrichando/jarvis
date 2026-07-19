import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

// The real model picker pulls @tanstack/react-query (needs a provider) —
// irrelevant to toggle behavior, so stub it out.
vi.mock("@/components/chat/model-picker", () => ({
  ComposerModelPicker: () => null,
}));

import { Composer } from "@/components/chat/composer";
import { getProviderUX } from "@/lib/ai/provider-ux";

// Regression guard for the "I don't have a web search tool" bug: the DeepSeek
// composer seeded `search: false` from `defaultOn: false`, which reached
// /api/chat as a literal `false` and dropped BOTH the webSearch tool and the
// server-side grounding — even though the user never touched the toggle.
// Web search must be AVAILABLE BY DEFAULT (mobile parity); only an explicit
// user toggle-off may send `search: false`.

const base = {
  value: "what's the latest news on quantum computing",
  onChange: () => {},
  status: "ready" as const,
  provider: "deepseek" as const,
  placeholder: "composer-test",
};

describe("web-search toggle default", () => {
  it("DeepSeek's Search inline toggle defaults ON; DeepThink stays OFF", () => {
    const inline = getProviderUX("deepseek").inlineToggles ?? [];
    expect(inline.find((t) => t.id === "search")?.defaultOn).toBe(true);
    expect(inline.find((t) => t.id === "deepthink")?.defaultOn).toBeFalsy();
  });

  it("untouched composer submits with search allowed (search: true on Enter)", () => {
    const onSubmit = vi.fn();
    render(<Composer {...base} onSubmit={onSubmit} />);
    fireEvent.keyDown(screen.getByPlaceholderText("composer-test"), {
      key: "Enter",
    });
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0][0]?.toggles).toMatchObject({ search: true });
  });

  it("explicit user toggle-off sends search: false (Enter path)", () => {
    const onSubmit = vi.fn();
    render(<Composer {...base} onSubmit={onSubmit} />);
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    fireEvent.keyDown(screen.getByPlaceholderText("composer-test"), {
      key: "Enter",
    });
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0][0]?.toggles).toMatchObject({ search: false });
  });

  it("toggling back on restores search: true", () => {
    const onSubmit = vi.fn();
    render(<Composer {...base} onSubmit={onSubmit} />);
    const pill = screen.getByRole("button", { name: "Search" });
    fireEvent.click(pill); // off
    fireEvent.click(pill); // back on
    fireEvent.keyDown(screen.getByPlaceholderText("composer-test"), {
      key: "Enter",
    });
    expect(onSubmit.mock.calls[0][0]?.toggles).toMatchObject({ search: true });
  });

  it("Send-button click carries the toggle state too (explicit off respected)", () => {
    // Before the fix, submitClick omitted `toggles` entirely, so an explicit
    // Search-off was silently dropped when submitting by mouse.
    const onSubmit = vi.fn();
    render(<Composer {...base} onSubmit={onSubmit} />);
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0][0]?.toggles).toMatchObject({ search: false });
  });

  it("Send-button click with untouched toggles sends search: true", () => {
    const onSubmit = vi.fn();
    render(<Composer {...base} onSubmit={onSubmit} />);
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0][0]?.toggles).toMatchObject({ search: true });
  });
});
