import { describe, expect, it } from "vitest";
import { ago, pct, sevTone, shortHost } from "../src/lib/format";
import { age, bytes, hours, ms, num, ringTone, stateTone } from "../src/lib/format";

describe("format", () => {
  it("renders relative time in four bands", () => {
    const now = Date.now();
    expect(ago(new Date(now - 42_000).toISOString())).toBe("42s ago");
    expect(ago(new Date(now - 7 * 60_000).toISOString())).toBe("7m ago");
    expect(ago(new Date(now - 3 * 3_600_000).toISOString())).toBe("3h ago");
    expect(ago(new Date(now - 2 * 86_400_000).toISOString())).toBe("2d ago");
  });
  it("maps severity to the only three tones", () => {
    expect([sevTone("CRIT"), sevTone("WARN"), sevTone("INFO")]).toEqual(["alert", "warn", "plain"]);
  });
  it("trims Nexus-style site names and clamps percentages", () => {
    expect(shortHost("Roots prod (status.example.org)")).toBe("Roots prod");
    expect(pct(99.456)).toBe("99.46%");
  });
});

describe("format — the rest of the vocabulary", () => {
  it("clamps and pads the numeric helpers", () => {
    expect([pct(-4), pct(140), pct(null)]).toEqual(["0%", "100%", "—"]);
    expect([ms(182), ms(1500), ms(null)]).toEqual(["182 ms", "1.5 s", "—"]);
    expect([bytes(900), bytes(2048), bytes(null)]).toEqual(["900 B", "2 KB", "—"]);
    expect([hours(2.5), hours(0.25), hours(3), hours(null)]).toEqual([
      "2h 30m",
      "15m",
      "3h",
      "—",
    ]);
    expect([num(12345), num(null)]).toEqual(["12,345", "—"]);
  });

  it("treats a missing or future timestamp as never / zero, not as a negative age", () => {
    expect(ago(null)).toBe("never");
    expect(ago("not a date")).toBe("never");
    expect(ago(new Date(Date.now() + 60_000).toISOString())).toBe("0s ago");
    expect([age(42), age(300), age(null)]).toEqual(["42s", "5m", "—"]);
  });

  it("maps only the hub's documented state words to a colour", () => {
    expect([stateTone("ok"), stateTone("warn"), stateTone("crit")]).toEqual([
      "good",
      "warn",
      "alert",
    ]);
    expect([stateTone("OFFLINE"), stateTone("planned"), stateTone("done")]).toEqual([
      "alert",
      "warn",
      "good",
    ]);
    // Anything the hub might add later stays colourless rather than inventing a hue.
    expect([stateTone("none"), stateTone(""), stateTone("something-new")]).toEqual([
      "plain",
      "plain",
      "plain",
    ]);
  });

  it("grades a score the same way the ring does", () => {
    expect([ringTone(83), ringTone(60), ringTone(20), ringTone(null)]).toEqual([
      "good",
      "warn",
      "alert",
      "plain",
    ]);
  });

  it("leaves a name without a parenthesised suffix alone", () => {
    expect(shortHost("cr1000a")).toBe("cr1000a");
    expect(shortHost("Roots prod (status.example.org) tail")).toBe("Roots prod (status.example.org) tail");
    expect(shortHost(null)).toBe("");
  });

  it("maps a null severity to no colour", () => {
    expect(sevTone(null)).toBe("plain");
  });
});

// Task 78: the reference console's `fmtAgo` bands, which Vault's `ago`/`age`
// did not share — it floored and cut at 60/3600/86400, so a figure sat a whole
// unit behind the truth at the top of every band.
describe("relative time matches the reference console's bands", () => {
  const now = Date.now();
  const at = (secs: number) => new Date(now - secs * 1000).toISOString();

  it("stays in seconds up to 90, not 60", () => {
    expect(ago(at(88))).toBe("88s ago");
    expect(ago(at(89))).toBe("89s ago");
    expect(ago(at(90))).toBe("2m ago");
  });

  it("stays in minutes up to 90 minutes, not 60", () => {
    expect(ago(at(3600))).toBe("60m ago");
    expect(ago(at(5399))).toBe("90m ago");
    expect(ago(at(5400))).toBe("2h ago");
  });

  it("stays in hours up to two days, not one", () => {
    expect(ago(at(86400))).toBe("24h ago");
    expect(ago(at(172799))).toBe("48h ago");
    expect(ago(at(172800))).toBe("2d ago");
  });

  it("never prints a bare 1m/1h/1d, because the cut and the rounding are half a unit apart", () => {
    // The reference has this too. 89s reads in seconds, 90s already rounds to
    // two minutes — so the first minute figure ever shown is "2m ago". It is
    // the trade for never showing a stale unit, not an off-by-one.
    expect(ago(at(89))).toBe("89s ago");
    expect(ago(at(90))).toBe("2m ago");
    expect(ago(at(5400))).toBe("2h ago");
    expect(ago(at(172800))).toBe("2d ago");
  });

  it("rounds to the nearest unit rather than flooring", () => {
    // Floored this read "1m ago" while it was nearly two minutes old.
    expect(ago(at(110))).toBe("2m ago");
    expect(ago(at(100))).toBe("2m ago");
  });

  it("gives `age` the same bands without the suffix", () => {
    expect(age(89)).toBe("89s");
    expect(age(90)).toBe("2m");
    expect(age(5400)).toBe("2h");
    expect(age(172800)).toBe("2d");
  });
});
