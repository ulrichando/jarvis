import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { DevicesView, groupByType, type IotDevice } from "@/components/devices/devices-view";

// The Devices tab renders the IoT sidecar's inventory truthfully: grouped by
// type, controllability worn as state, and control buttons DISABLED with the
// control_hint as the reason whenever a device isn't locally controllable.

const dev = (over: Partial<IotDevice> = {}): IotDevice => ({
  ip: "192.168.1.9",
  mac: "aa:bb:cc:dd:ee:ff",
  hostname: "roku.local",
  name: "Roku TV",
  type: "tv",
  brand: "Roku",
  protocol: ["ssdp"],
  controllable: "local",
  control_hint: "Roku ECP",
  first_seen: 1_700_000_000,
  last_seen: 1_700_000_100,
  ...over,
});

const FLEET: IotDevice[] = [
  dev(),
  dev({
    name: "Echo Dot",
    type: "speaker",
    brand: "Amazon Alexa",
    mac: "11:22:33:44:55:66",
    ip: "192.168.1.20",
    hostname: null,
    controllable: "cloud_only",
    control_hint: "Alexa — not locally controllable",
  }),
  dev({
    name: "Desk Bulb",
    type: "light",
    brand: "Tuya (Smart Life)",
    mac: null,
    ip: "192.168.1.30",
    hostname: null,
    protocol: ["tuya"],
    controllable: "cloud_only",
    control_hint: "needs local key or Home Assistant",
  }),
];

function stubFetch(body: unknown = { devices: [] }) {
  const fn = vi.fn(
    async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
  );
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("groupByType", () => {
  it("groups devices by type", () => {
    const groups = groupByType(FLEET);
    const types = groups.map(([t]) => t);
    expect(types).toContain("tv");
    expect(types).toContain("speaker");
    expect(types).toContain("light");
    expect(groups.find(([t]) => t === "tv")?.[1]).toHaveLength(1);
  });
});

describe("<DevicesView />", () => {
  it("renders a passed-in device array grouped by type", () => {
    stubFetch();
    render(<DevicesView initialDevices={FLEET} />);

    // All devices visible
    expect(screen.getByText("Roku TV")).toBeInTheDocument();
    expect(screen.getByText("Echo Dot")).toBeInTheDocument();
    expect(screen.getByText("Desk Bulb")).toBeInTheDocument();

    // Type group headings
    expect(screen.getByText("TVs")).toBeInTheDocument();
    expect(screen.getByText("Speakers")).toBeInTheDocument();
    expect(screen.getByText("Lights")).toBeInTheDocument();
  });

  it("disables the control button and shows the control_hint for non-local devices", () => {
    stubFetch();
    render(<DevicesView initialDevices={FLEET} />);

    // Expand the cloud-only Echo Dot row to reveal its controls
    fireEvent.click(screen.getByRole("button", { name: /echo dot/i }));

    const echoPower = screen.getByRole("button", { name: /power — echo dot/i });
    expect(echoPower).toBeDisabled();
    expect(screen.getByText("Alexa — not locally controllable")).toBeInTheDocument();

    // A locally-controllable device's control stays enabled
    fireEvent.click(screen.getByRole("button", { name: /roku tv/i }));
    expect(screen.getByRole("button", { name: /power — roku tv/i })).not.toBeDisabled();
  });

  it("shows per-device detail (network identifiers) when a row is expanded", () => {
    stubFetch();
    render(<DevicesView initialDevices={FLEET} />);

    fireEvent.click(screen.getByRole("button", { name: /roku tv/i }));
    expect(screen.getByText("roku.local")).toBeInTheDocument();
    expect(screen.getByText("aa:bb:cc:dd:ee:ff")).toBeInTheDocument();
  });

  it("Rescan posts to /api/iot/scan", async () => {
    const fetchMock = stubFetch({ devices: FLEET });
    render(<DevicesView initialDevices={[]} />);

    fireEvent.click(screen.getByRole("button", { name: /rescan|scan network/i }));

    expect(fetchMock).toHaveBeenCalled();
    const [target, init] = fetchMock.mock.calls[0]! as [string, RequestInit];
    expect(String(target)).toBe("/api/iot/scan");
    expect(init?.method).toBe("POST");

    // Scan result lands in the list
    expect(await screen.findByText("Roku TV")).toBeInTheDocument();
  });
});
