import { describe, it, expect } from "vitest";
import { createRef } from "react";
import { render } from "@testing-library/react";
import { NoVNCView, type NoVNCHandle } from "@/components/computer-use/novnc-view";

describe("NoVNCView snapshot()", () => {
  it("returns null when no canvas is present", () => {
    const ref = createRef<NoVNCHandle>();
    render(<NoVNCView ref={ref} wsUrl="" password="" />);
    expect(ref.current?.snapshot()).toBeNull();
  });
});
