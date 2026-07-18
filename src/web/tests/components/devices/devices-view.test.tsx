import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// jsdom has no PointerEvent; base-ui's Switch touches it in its click path.
if (typeof PointerEvent === "undefined") {
  class PointerEventPolyfill extends MouseEvent {
    pointerId: number;
    pointerType: string;
    constructor(type: string, params: PointerEventInit = {}) {
      super(type, params);
      this.pointerId = params.pointerId ?? 0;
      this.pointerType = params.pointerType ?? "";
    }
  }
  Object.assign(globalThis, { PointerEvent: PointerEventPolyfill });
  Object.assign(window, { PointerEvent: PointerEventPolyfill });
}

import { DevicesView, deviceKey, groupByType, type IotDevice } from "@/components/devices/devices-view";

// Phase 2: the Devices tab is a categorized CONTROL UI. Devices group by
// category, controls render strictly from `capabilities`, uncontrollable
// devices list with their control_hint as a muted reason (no fake controls),
// and the Home Assistant connect panel sits below the inventory.

const dev = (over: Partial<IotDevice> = {}): IotDevice => ({
  ip: "192.168.1.9",
  mac: "aa:bb:cc:dd:ee:ff",
  hostname: "lgwebostv.local",
  name: "LG TV",
  type: "tv",
  brand: "LG",
  protocol: ["ssdp", "webos"],
  controllable: "local",
  control_hint: "LG webOS — local control",
  id: null,
  capabilities: ["power", "volume", "mute", "apps", "inputs", "media"],
  first_seen: 1_700_000_000,
  last_seen: 1_700_000_100,
  ...over,
});

const FLEET: IotDevice[] = [
  dev(),
  dev({
    name: "Kitchen Light",
    type: "light",
    brand: "Home Assistant",
    id: "ha:light.kitchen",
    mac: null,
    ip: "192.168.1.50",
    hostname: null,
    protocol: ["home_assistant"],
    capabilities: ["on", "off", "brightness", "color"],
    control_hint: "via Home Assistant",
  }),
  dev({
    name: "Echo Dot",
    type: "speaker",
    brand: "Amazon Alexa",
    mac: "11:22:33:44:55:66",
    ip: "192.168.1.20",
    hostname: null,
    protocol: ["mdns"],
    controllable: "cloud_only",
    capabilities: [],
    control_hint: "Alexa — not locally controllable",
  }),
];

function stubFetch(body: unknown = { devices: [] }) {
  const fn = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    // The HA panel loads /api/iot/config on mount — answer it distinctly.
    if (String(input).includes("/api/iot/config")) {
      return new Response(
        JSON.stringify({ home_assistant: { url: "", token_set: false } }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("deviceKey", () => {
  it("prefers id, then mac, then ip", () => {
    expect(deviceKey(dev({ id: "ha:light.kitchen" }))).toBe("ha:light.kitchen");
    expect(deviceKey(dev({ id: null }))).toBe("aa:bb:cc:dd:ee:ff");
    expect(deviceKey(dev({ id: null, mac: null }))).toBe("ip:192.168.1.9");
  });
});

describe("groupByType", () => {
  it("groups devices by category", () => {
    const groups = groupByType(FLEET);
    const types = groups.map(([t]) => t);
    expect(types).toContain("tv");
    expect(types).toContain("speaker");
    expect(types).toContain("light");
    expect(groups.find(([t]) => t === "tv")?.[1]).toHaveLength(1);
  });
});

describe("<DevicesView />", () => {
  it("renders devices grouped under category headings", () => {
    stubFetch();
    render(<DevicesView initialDevices={FLEET} />);

    expect(screen.getByText("LG TV")).toBeInTheDocument();
    expect(screen.getByText("Kitchen Light")).toBeInTheDocument();
    expect(screen.getByText("Echo Dot")).toBeInTheDocument();

    expect(screen.getByText("TVs")).toBeInTheDocument();
    expect(screen.getByText("Lights")).toBeInTheDocument();
    expect(screen.getByText("Speakers")).toBeInTheDocument();
  });

  it("shows a power toggle only for capability-bearing devices; uncontrollable ones wear their hint", () => {
    stubFetch();
    render(<DevicesView initialDevices={FLEET} />);

    // Controllable devices get a quick power switch in the row.
    expect(screen.getByRole("switch", { name: /power — lg tv/i })).toBeInTheDocument();
    expect(
      screen.getByRole("switch", { name: /power — kitchen light/i }),
    ).toBeInTheDocument();

    // The capability-less Echo Dot gets NO switch — expanding shows the hint.
    expect(screen.queryByRole("switch", { name: /power — echo dot/i })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /echo dot — details/i }));
    expect(screen.getByText("Alexa — not locally controllable")).toBeInTheDocument();
  });

  it("power toggle POSTs the right command (webOS power_on, HA on)", async () => {
    const fetchMock = stubFetch();
    render(<DevicesView initialDevices={FLEET} />);

    fireEvent.click(screen.getByRole("switch", { name: /power — lg tv/i }));
    fireEvent.click(screen.getByRole("switch", { name: /power — kitchen light/i }));

    await vi.waitFor(() => {
      const commands = fetchMock.mock.calls.filter(([u]) =>
        String(u).includes("/command"),
      );
      expect(commands).toHaveLength(2);
    });

    const commands = fetchMock.mock.calls.filter(([u]) =>
      String(u).includes("/command"),
    ) as [string, RequestInit][];

    const tvCall = commands.find(([u]) =>
      u.includes(encodeURIComponent("aa:bb:cc:dd:ee:ff")),
    )!;
    expect(tvCall[0]).toBe(
      `/api/iot/devices/${encodeURIComponent("aa:bb:cc:dd:ee:ff")}/command`,
    );
    expect(JSON.parse(String(tvCall[1].body))).toEqual({ action: "power_on" });

    const lightCall = commands.find(([u]) =>
      u.includes(encodeURIComponent("ha:light.kitchen")),
    )!;
    expect(JSON.parse(String(lightCall[1].body))).toEqual({ action: "on" });
  });

  it("expanding a controllable device reveals capability-driven controls", () => {
    stubFetch();
    render(<DevicesView initialDevices={FLEET} />);

    fireEvent.click(screen.getByRole("button", { name: /kitchen light — details/i }));
    expect(
      screen.getByRole("slider", { name: /brightness — kitchen light/i }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/color — kitchen light/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /lg tv — details/i }));
    expect(screen.getByRole("slider", { name: /volume — lg tv/i })).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: /mute — lg tv/i })).toBeInTheDocument();
    // webOS TV → one-time pairing affordance.
    expect(screen.getByRole("button", { name: /^pair$/i })).toBeInTheDocument();
  });

  it("brightness slider commits a debounced brightness command", async () => {
    const fetchMock = stubFetch();
    render(<DevicesView initialDevices={FLEET} />);

    fireEvent.click(screen.getByRole("button", { name: /kitchen light — details/i }));
    fireEvent.change(
      screen.getByRole("slider", { name: /brightness — kitchen light/i }),
      { target: { value: "40" } },
    );

    await vi.waitFor(() => {
      const cmd = fetchMock.mock.calls.find(
        ([u]) =>
          String(u).includes(encodeURIComponent("ha:light.kitchen")) &&
          String(u).includes("/command"),
      );
      expect(cmd).toBeTruthy();
      expect(JSON.parse(String((cmd![1] as RequestInit).body))).toEqual({
        action: "brightness",
        brightness_pct: 40,
      });
    });
  });

  it("Pair posts to /connect", async () => {
    const fetchMock = stubFetch();
    render(<DevicesView initialDevices={FLEET} />);

    fireEvent.click(screen.getByRole("button", { name: /lg tv — details/i }));
    fireEvent.click(screen.getByRole("button", { name: /^pair$/i }));

    await vi.waitFor(() => {
      const connect = fetchMock.mock.calls.find(([u]) =>
        String(u).includes("/connect"),
      );
      expect(connect).toBeTruthy();
      expect(String(connect![0])).toBe(
        `/api/iot/devices/${encodeURIComponent("aa:bb:cc:dd:ee:ff")}/connect`,
      );
      expect((connect![1] as RequestInit).method).toBe("POST");
    });
  });

  it("renders the Home Assistant connect panel", () => {
    stubFetch();
    render(<DevicesView initialDevices={FLEET} />);
    // The panel title also matches the Kitchen Light's brand line — assert
    // on the panel's own subtitle instead.
    expect(
      screen.getByText("Bring in lights, plugs, thermostats, and media players"),
    ).toBeInTheDocument();
  });

  it("Rescan posts to /api/iot/scan", async () => {
    const fetchMock = stubFetch({ devices: FLEET });
    render(<DevicesView initialDevices={[]} />);

    fireEvent.click(screen.getByRole("button", { name: /rescan|scan network/i }));

    // The HA panel's /config load also fires on mount — find the scan call.
    const scan = fetchMock.mock.calls.find(
      ([u]) => String(u) === "/api/iot/scan",
    );
    expect(scan).toBeTruthy();
    expect((scan![1] as RequestInit).method).toBe("POST");

    // Scan result lands in the list
    expect(await screen.findByText("LG TV")).toBeInTheDocument();
  });
});
