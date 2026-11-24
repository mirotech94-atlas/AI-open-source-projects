import { describe, expect, it } from "vitest";
import { createPoller, withTimeout } from "./livePoll";

/** A fake clock and a controllable read. */
function rig(visible = true) {
  const state = { visible, ticks: [] as (() => void)[], loads: 0, release: [] as (() => void)[] };
  const poller = createPoller({
    load: () => new Promise<void>((res) => { state.loads += 1; state.release.push(res); }),
    visible: () => state.visible,
    every: (fn) => { state.ticks.push(fn); return state.ticks.length; },
    cancel: () => { state.ticks = []; },
  }, 10_000);
  const tick = (): void => { state.ticks.forEach((f) => f()); };
  const finish = async (): Promise<void> => {
    state.release.splice(0).forEach((r) => r());
    await new Promise((r) => setTimeout(r, 0));
  };
  return { state, poller, tick, finish };
}

describe("livePoll", () => {
  it("reads once on start, then once per tick", async () => {
    const r = rig();
    r.poller.start();
    expect(r.state.loads).toBe(1);
    await r.finish();
    r.tick();
    expect(r.state.loads).toBe(2);
  });

  it("never overlaps: a tick while a read is in flight is dropped", async () => {
    const r = rig();
    r.poller.start();
    r.tick(); r.tick();
    expect(r.state.loads).toBe(1);
    await r.finish();
    r.tick();
    expect(r.state.loads).toBe(2);
  });

  it("pauses while the tab is hidden and reads at once on return", async () => {
    const r = rig(false);
    r.poller.start();
    r.tick();
    expect(r.state.loads).toBe(0);
    r.state.visible = true;
    expect(r.poller.poke()).toBe(true);     // focus / visibilitychange
    expect(r.state.loads).toBe(1);
  });

  it("a read that throws still frees the slot", async () => {
    let n = 0;
    const p = createPoller({
      load: () => { n += 1; return Promise.reject(new Error("down")); },
      visible: () => true, every: () => 1, cancel: () => undefined,
    }, 10_000);
    p.start();
    await new Promise((r) => setTimeout(r, 0));
    expect(p.poke()).toBe(true);
    expect(n).toBe(2);
  });

  it("stops ticking after stop()", async () => {
    const r = rig();
    r.poller.start();
    await r.finish();
    r.poller.stop();
    expect(r.poller.poke()).toBe(false);
  });

  it("withTimeout turns a hung read into a failed one", async () => {
    await expect(withTimeout(new Promise<never>(() => undefined), 20, "overview"))
      .rejects.toThrow("timed out");
    await expect(withTimeout(Promise.resolve(3), 50, "x")).resolves.toBe(3);
  });
});
