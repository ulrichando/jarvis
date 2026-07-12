import { DevicesView } from "@/components/devices/devices-view";

// Devices — inventory of everything the IoT discovery sidecar can see on the
// LAN, via the authed /api/iot/* forwarder. Login-gated by proxy.ts.
export default function DevicesPage() {
  return <DevicesView />;
}
