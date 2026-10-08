import { describe, expect, it } from "vitest";
import { sensorIsLive } from "../src/lib/sensorLive";

describe("sensorIsLive", () => {
  it("counts an enrolled sensor that reported recently", () => {
    expect(sensorIsLive({ status: "active", age_s: 279 })).toBe(true);
  });
  it("does not count a silent, pending or revoked sensor", () => {
    expect(sensorIsLive({ status: "active", age_s: 3600 })).toBe(false);
    expect(sensorIsLive({ status: "active", age_s: null })).toBe(false);
    expect(sensorIsLive({ status: "pending", age_s: 10 })).toBe(false);
    expect(sensorIsLive({ status: "revoked", age_s: 10 })).toBe(false);
  });
});
