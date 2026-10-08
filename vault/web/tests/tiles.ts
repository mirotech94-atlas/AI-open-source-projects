// The Devices tile block, for a fixture that only cares about the grid below
// it. Nine tiles' worth of "nothing measured": counts are 0 because 0 devices
// really is 0, and every internet figure is null because a fixture has not
// measured one.
import type { DeviceTiles } from "../src/types";

export const NO_TILES: DeviceTiles = {
  new_devices: 0,
  trusted: 0,
  randomized: 0,
  down_mbps: null,
  up_mbps: null,
  latency_ms: null,
  dns_ms: null,
  dns_ok: null,
  wan_ip: "",
  wan_ips: [],
};
