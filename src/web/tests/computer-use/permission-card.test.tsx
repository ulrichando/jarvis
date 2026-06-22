import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { PermissionCard } from "@/components/computer-use/permission-card";

describe("PermissionCard", () => {
  it("fires onApprove with the chosen decision", () => {
    const onApprove = vi.fn();
    render(<PermissionCard part={{ kind: "permission", reqId: "r1", label: "type a URL", text: 'type "x"' }} onApprove={onApprove} />);
    fireEvent.click(screen.getByText("For session"));
    expect(onApprove).toHaveBeenCalledWith("r1", "session");
  });
  it("shows a resolved state instead of buttons", () => {
    render(<PermissionCard part={{ kind: "permission", reqId: "r1", label: "x", text: "", resolved: "deny" }} onApprove={() => {}} />);
    expect(screen.getByText(/Denied/)).toBeTruthy();
    expect(screen.queryByText("Approve")).toBeNull();
  });
});
