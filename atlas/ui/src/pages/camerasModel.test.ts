/**
 * The Cameras tab: its pure model, and the page to static markup. Twelve
 * cards always; a missing or old frame is UNKNOWN, never OK; every figure
 * drawn carries the time it was read.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Cameras, CamerasHead, CamerasTabs, CamerasView, ClipsPanel, CoveragePanel, PeoplePanel, RuleOverlay } from "./Cameras";
import {
  CAMERA_NAMES, FRAME_MARGIN_S, alertBadge, eventLabelText, forgetReady, peopleView, validName,
  type People, cardHealth, cardStyleClass, eventLine, formatClock,
  type Coverage, type CoverageRow, type RuleInfo, coverageSide, overlayShapes, ruleLine, twelveCoverage,
  cameraMetrics, clipLine, motionLine, nvrLine, twelveCards, MODES, MODE_HELP, kindText, normaliseMode, type CameraCard, type CamerasOverview,
} from "./camerasModel";

const NOW = new Date(2026, 10, 12, 9, 5, 30);            // local 09:05:30
const iso = (s: number): string => new Date(NOW.getTime() - s * 1000).toISOString();

function card(n: number, over: Partial<CameraCard> = {}): CameraCard {
  return {
    channel: n - 1, number: n, name: CAMERA_NAMES[n - 1] ?? `Camera ${n}`, model: "IPC-X",
    online: true, health: "ok", reason: null, last_motion_at: iso(120),
    last_motion_kind: "person", frame_at: iso(5), read_at: iso(5),
    snapshot_url: `/api/cameras/${n - 1}/snap`, snapshot_max_age_s: 30, ...over,
  };
}

function overview(over: Partial<CamerasOverview> = {}): CamerasOverview {
  return {
    served_at: iso(0), mode: "home",
    nvr: { health: "ok", reason: null, read_at: iso(5) },
    cameras: Array.from({ length: 12 }, (_, i) => card(i + 1)), ...over,
  };
}

describe("twelveCards", () => {
  it("is twelve UNKNOWN cards, named and numbered, when nothing was read", () => {
    const cards = twelveCards(null);
    expect(cards).toHaveLength(12);
    expect(cards.map((c) => c.number)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]);
    expect(cards.map((c) => c.name)).toEqual([
      "Left Siding", "Right Pool", "Top Main Street", "Left Pool", "1st Driveway",
      "2nd Left Siding", "Main Door", "Right Siding", "2nd Driveway", "3rd Driveway",
      "2nd Main Door", "Garage"]);
    for (const c of cards) {
      expect(c.health).toBe("unknown");
      expect(c.read_at).toBeNull();
      expect(c.frame_at).toBeNull();
      expect(c.online).toBeNull();
    }
  });

  it("fills a missing camera with an UNKNOWN card and keeps order", () => {
    const o = overview({ cameras: [card(3), card(1)] });
    const cards = twelveCards(o);
    expect(cards).toHaveLength(12);
    expect(cards[0]?.number).toBe(1);
    expect(cards[0]?.health).toBe("ok");
    expect(cards[1]?.health).toBe("unknown");
    expect(cards[2]?.number).toBe(3);
  });

  it("ignores cameras outside 1..12 and duplicates", () => {
    const o = overview({ cameras: [card(1), card(1, { name: "dup" }), card(13)] });
    expect(twelveCards(o)).toHaveLength(12);
    expect(twelveCards(o)[0]?.name).toBe("Left Siding");
  });
});

describe("cardHealth", () => {
  it("is ok for a fresh frame", () => {
    expect(cardHealth(card(1), NOW)).toBe("ok");
  });
  it("turns a frame older than max age plus the margin into unknown", () => {
    const limit = 30 + FRAME_MARGIN_S;
    expect(FRAME_MARGIN_S).toBe(15);
    expect(cardHealth(card(1, { frame_at: iso(limit - 1) }), NOW)).toBe("ok");
    expect(cardHealth(card(1, { frame_at: iso(limit + 1) }), NOW)).toBe("unknown");
  });
  it("turns any missing read time into unknown", () => {
    expect(cardHealth(card(1, { frame_at: null }), NOW)).toBe("unknown");
    expect(cardHealth(card(1, { read_at: null as unknown as string }), NOW)).toBe("unknown");
    expect(cardHealth(card(1, { read_at: "not a time" }), NOW)).toBe("unknown");
  });
  it("keeps failing and unknown as the server said them", () => {
    expect(cardHealth(card(1, { health: "failing" }), NOW)).toBe("failing");
    expect(cardHealth(card(1, { health: "unknown" }), NOW)).toBe("unknown");
    expect(cardHealth(card(1, { health: "bogus" as never }), NOW)).toBe("unknown");
  });
  it("keeps an offline camera failing although it has no frame", () => {
    const offline = card(3, { online: false, health: "failing", frame_at: null });
    expect(cardHealth(offline, NOW)).toBe("failing");
    expect(cardStyleClass(cardHealth(offline, NOW))).toBe("cam-failing");
    expect(cardHealth({ ...offline, read_at: null as unknown as string }, NOW))
      .toBe("unknown");
  });
});

describe("style class", () => {
  it("never maps unknown to the OK class", () => {
    expect(cardStyleClass("unknown")).not.toBe(cardStyleClass("ok"));
    expect(cardStyleClass("unknown")).toContain("hatch-edge");
    expect(cardStyleClass("unknown")).not.toContain("cam-ok");
    expect(cardStyleClass("ok")).toContain("cam-ok");
    expect(cardStyleClass("failing")).not.toContain("cam-ok");
  });
});

describe("formatting", () => {
  it("formats read times as local HH:MM:SS", () => {
    expect(formatClock(new Date(2026, 10, 12, 9, 5, 7).toISOString())).toBe("09:05:07");
    expect(formatClock(new Date(2026, 10, 12, 23, 59, 0).toISOString())).toBe("23:59:00");
  });
  it("says unread, never a time, when there is none", () => {
    expect(formatClock(null)).toBe("not read");
    expect(formatClock("garbage")).toBe("not read");
  });
  it("motion line carries kind and time, or says none", () => {
    expect(motionLine(card(1, { last_motion_at: new Date(2026, 10, 12, 8, 0, 1).toISOString(),
                                last_motion_kind: "vehicle" }))).toBe("vehicle at 08:00:01");
    expect(motionLine(card(1, { last_motion_at: null, last_motion_kind: null })))
      .toBe("no motion read");
  });
  it("event line has a time", () => {
    const e = { id: 1, at: new Date(2026, 10, 12, 8, 1, 2).toISOString(), channel: 0, number: 1,
                name: "Left Siding", kind: "person" as const, label: "unlabelled", cluster_id: null, alert_level: 0,
                read_at: iso(1), snapshot_url: null };
    expect(eventLine(e)).toBe("08:01:02 · Camera 1 Left Siding · \u{1F6B6} person · unlabelled");
    expect(eventLine({ ...e, kind: "vehicle" })).toContain("\u{1F697} car");
    expect(kindText("animal")).toContain("animal");
    expect(kindText("motion")).toContain("motion");
  });
  it("NVR line carries health and read time, or says unread", () => {
    expect(nvrLine({ health: "ok", reason: null, read_at: new Date(2026, 10, 12, 9, 0, 0).toISOString() }))
      .toBe("NVR OK · read 09:00:00");
    expect(nvrLine(null)).toBe("NVR Unknown · not read");
    expect(nvrLine({ health: "ok", reason: null, read_at: null })).toBe("NVR Unknown · not read");
  });
});

describe("page", () => {
  it("renders twelve UNKNOWN cards, with the word, when the call failed", () => {
    const html = renderToStaticMarkup(createElement(CamerasView, {
      overview: null, now: NOW, error: "503", mode: null,
      onOpen: () => undefined, onMode: () => undefined }));
    expect((html.match(/class="cam-card /g) ?? []).length).toBe(12);
    expect((html.match(/data-health="unknown"/g) ?? []).length).toBeGreaterThanOrEqual(12);
    expect(html).toContain("Unknown");
    expect(html).not.toContain('data-health="ok"');
    expect(html).toContain("Left Siding");
    expect(html).toContain("Garage");
  });

  it("every figure rendered carries a read time", () => {
    const o = overview({ cameras: [card(1)] });
    const html = renderToStaticMarkup(createElement(CamerasView, {
      overview: o, now: NOW, error: null, mode: "home",
      onOpen: () => undefined, onMode: () => undefined }));
    // one 'read HH:MM:SS' for the frame and one for the card, per card with a frame
    const cards = html.split('class="cam-card ').slice(1);
    expect(cards).toHaveLength(12);
    for (const c of cards) {
      expect(c).toMatch(/read \d\d:\d\d:\d\d|not read/);
    }
    const first = cards[0] ?? "";
    expect((first.match(/read \d\d:\d\d:\d\d/g) ?? []).length).toBe(2);
    expect(html).toMatch(/NVR OK · read \d\d:\d\d:\d\d/);
  });

  it("a stale frame renders UNKNOWN, not OK", () => {
    const o = overview({ cameras: [card(1, { frame_at: iso(600) })] });
    const html = renderToStaticMarkup(createElement(CamerasView, {
      overview: o, now: NOW, error: null, mode: "home",
      onOpen: () => undefined, onMode: () => undefined }));
    const first = html.split('class="cam-card ')[1] ?? "";
    expect(first).toContain('data-health="unknown"');
    expect(first).not.toContain('data-health="ok"');
  });
});

const people = (over: Partial<People> = {}): People => ({
  served_at: iso(0), read_at: iso(2),
  engine: { state: "ready", reason: null }, key: { state: "ready", reason: null },
  slots: [{ slot: 1, name: "Ana", clusters: 1 }, { slot: 2, name: null, clusters: 0 }],
  clusters: [
    { id: "c1", status: "unknown", slot: null, sightings: 3, first_seen: iso(600),
      last_seen: iso(60), event_id: 7, snapshot_url: "/api/cameras/faces/c1.jpg",
      channel_name: "Main Door", card_id: 9, card_state: "pending" },
    { id: "c2", status: "unknown", slot: null, sightings: 1, first_seen: iso(900),
      last_seen: iso(900), event_id: null, snapshot_url: null, channel_name: null,
      card_id: 8, card_state: "expired" },
  ], ...over,
});

describe("people model", () => {
  it("an unreadable read is UNKNOWN with no slots or clusters, not an empty list", () => {
    const v = peopleView(null);
    expect(v.readable).toBe(false);
    expect(v.empty).toBe(false);
    expect(v.readLine).toBe("People not read");
    expect(v.chips.every((c) => c.unknown)).toBe(true);
    expect(peopleView(people({ read_at: "" })).readable).toBe(false);
  });
  it("engine and key unknown show the word and the reason", () => {
    const v = peopleView(people({
      engine: { state: "unknown", reason: "model missing" }, key: { state: "ready", reason: null } }));
    expect(v.chips[0]).toMatchObject({ unknown: true, text: "Face engine unknown · model missing" });
    expect(v.chips[1]).toMatchObject({ unknown: false, text: "Key ready" });
  });
  it("always five slots; an empty one says name not set yet, no name invented", () => {
    const v = peopleView(people());
    expect(v.slots).toHaveLength(5);
    expect(v.slots[0]?.text).toBe("Slot 1 - Ana");
    expect(v.slots[1]?.text).toBe("Slot 2 - name not set yet");
    expect(v.slots[4]?.name).toBeNull();
  });
  it("pending card points to the Approvals board; expired says nobody was marked known", () => {
    const v = peopleView(people());
    expect(v.clusters[0]?.note).toBe("Decide this card on the Approvals board (or from WhatsApp)");
    expect(v.clusters[0]?.cardLink).toBe(true);
    expect(v.clusters[1]?.note).toBe("expired - nobody was marked known");
    expect(v.clusters[1]?.cardLink).toBe(false);
  });
  it("labels and alert badges", () => {
    expect(eventLabelText("household: Ana")).toBe("household: Ana");
    expect(eventLabelText("")).toBe("unlabelled");
    expect(alertBadge(3)).toEqual({ text: "serious (3)", tone: "serious" });
    expect(alertBadge(0).tone).toBe("none");
    expect(alertBadge(1).tone).toBe("low");
    expect(alertBadge(2).tone).toBe("mid");
    expect(alertBadge(9).text).toBe("level unknown");
    expect(alertBadge(undefined).text).toBe("level unknown");
  });
  it("name and forget guards", () => {
    expect(validName("  ")).toBeNull();
    expect(validName(" Ana ")).toBe("Ana");
    expect(validName("x".repeat(41))).toBeNull();
    expect(forgetReady("forget")).toBe(false);
    expect(forgetReady("FORGET")).toBe(true);
  });
});

describe("people panel", () => {
  const noop = (): void => undefined;
  const props = { onSaveName: noop, onAssign: noop, onHousehold: noop, onForget: noop, busy: false,
    error: null, removed: null };
  it("renders unreadable as UNKNOWN with the word", () => {
    const html = renderToStaticMarkup(createElement(PeoplePanel, { ...props, view: peopleView(null) }));
    expect(html).toContain("Unknown");
    expect(html).toContain("People not read");
    expect(html).not.toContain("Slot 1");
  });
  it("renders slots, clusters, thumbnail, approvals link and forget section", () => {
    const html = renderToStaticMarkup(createElement(PeoplePanel,
      { ...props, view: peopleView(people()), removed: 4 }));
    expect(html).toContain("Slot 2 - name not set yet");
    expect(html).toContain('src="/api/cameras/faces/c1.jpg"');
    expect(html).toContain('href="/agency"');
    expect(html).toContain("expired - nobody was marked known");
    expect(html).toContain("Forget face data");
    expect(html).toContain("Removed 4");
    expect(html).toMatch(/read \d\d:\d\d:\d\d/);
  });
  it("unknown groups get the household button and slot choice, household ones do not", () => {
    const html = renderToStaticMarkup(createElement(PeoplePanel, {
      ...props, onHousehold: noop,
      view: peopleView(people({ clusters: [
        { id: "c1", status: "unknown", slot: null, sightings: 1, first_seen: iso(9),
          last_seen: iso(9), event_id: null, snapshot_url: null, channel_name: null,
          card_id: null, card_state: null },
        { id: "c3", status: "household", slot: 1, sightings: 1, first_seen: iso(9),
          last_seen: iso(9), event_id: null, snapshot_url: null, channel_name: null,
          card_id: null, card_state: null }] })) }));
    expect(html.match(/This is a household member/g)).toHaveLength(1);
    expect(html).toContain("Household person for c1");
    expect(html).not.toContain("Household person for c3");
  });
  it("a pending card still shows the button, and busy disables it", () => {
    const html = renderToStaticMarkup(createElement(PeoplePanel,
      { ...props, onHousehold: noop, busy: true, view: peopleView(people()) }));
    expect(html.match(/This is a household member/g)).toHaveLength(2);
    expect(html).toMatch(/disabled=""[^>]*>This is a household member/);
  });
  it("no longer renders the motion events or the WhatsApp alerts list", () => {
    const html = renderToStaticMarkup(createElement(CamerasView, {
      overview: overview(), now: NOW, error: null, mode: "home", onOpen: noop, onMode: noop }));
    expect(html).not.toContain("Motion events");
    expect(html).not.toContain("Alerts sent to WhatsApp");
  });
  it("shows only Home and Away, each with a plain line", () => {
    expect(MODES).toEqual(["home", "away"]);
    expect(normaliseMode("night")).toBe("home");
    expect(normaliseMode("away")).toBe("away");
    const html = renderToStaticMarkup(createElement(CamerasView, {
      overview: overview(), now: NOW, error: null,
      mode: "home", onOpen: noop, onMode: noop }));
    expect(html).toContain(">Home<");
    expect(html).toContain(">Away<");
    expect(html).not.toMatch(/night/i);
    expect(html).toContain(MODE_HELP.home.slice(0, 20));
    expect(html).toContain("Serious:");
  });
});

describe("clips panel", () => {
  const clip = { at: iso(5), channel: 6, number: 7, name: "Main Door", size: 629145,
    read_at: iso(0), url: "/api/cameras/clips/2026-11-12/07-030005.mp4" };
  it("lists a clip with camera, time and size, and plays the newest", () => {
    const html = renderToStaticMarkup(createElement(ClipsPanel, {
      clips: { served_at: iso(0), clips: [clip] } }));
    expect(clipLine(clip)).toMatch(/\d\d:\d\d:\d\d · Camera 7 Main Door · 0\.6 MB/);
    expect(html).toContain('src="/api/cameras/clips/2026-11-12/07-030005.mp4"');
    expect(html).toContain("<video");
    expect(html).toContain("Main Door");
  });
  it("says so when there are none or they could not be read", () => {
    const none = renderToStaticMarkup(createElement(ClipsPanel, {
      clips: { served_at: iso(0), clips: [] } }));
    expect(none).toContain("No clips yet");
    expect(none).not.toContain("<video");
    expect(renderToStaticMarkup(createElement(ClipsPanel, { clips: null })))
      .toContain("Clips not read.");
  });
});

function rule(over: Partial<RuleInfo> = {}): RuleInfo {
  return {
    label: "person entering the white gate", subject: "person", test: "cross", direction: "enter",
    needs_check: true, zone: null, line: { a: [0.62, 0.44], b: [0.78, 0.44], inside: [0.7, 0.3] },
    near: null, health: "unknown", reason: "direction needs a frame every 4s or better", ...over,
  };
}

function covRow(n: number, over: Partial<CoverageRow> = {}): CoverageRow {
  return {
    channel: n - 1, number: n, name: CAMERA_NAMES[n - 1] ?? `Camera ${n}`,
    recorder: { health: "ok", motion_on: true, ai_supported: true, sensitivity: 20,
      reason: null, read_at: iso(5) },
    atlas: { health: "ok", running: true, reason: null, read_at: iso(5) },
    busy_frames: 0, rules: [rule()], suggestion: null, ...over,
  };
}

function coverage(over: Partial<Coverage> = {}): Coverage {
  return {
    served_at: iso(0), read_at: iso(5), atlas_detection: { enabled: true, interval_s: 1 },
    rules_note: "needs the owner check: every zone and line is a first guess.",
    cameras: Array.from({ length: 12 }, (_, i) => covRow(i + 1)), ...over,
  };
}

describe("coverage model", () => {
  it("a failed read is twelve UNKNOWN rows, never an empty panel", () => {
    const rows = twelveCoverage(null);
    expect(rows).toHaveLength(12);
    expect(rows.every((r) => r.recorder.health === "unknown" && r.atlas.health === "unknown")).toBe(true);
    expect(rows[2]?.name).toBe("Top Main Street");
  });
  it("a side with no read time is UNKNOWN even if the server said ok", () => {
    const s = coverageSide({ health: "ok", reason: null, read_at: null });
    expect(s.health).toBe("unknown");
    expect(s.text).toContain("Unknown");
    expect(s.text).toContain("not read");
  });
  it("an OK side shows when it was read", () => {
    const s = coverageSide({ health: "ok", reason: null, read_at: iso(5) });
    expect(s.health).toBe("ok");
    expect(s.text).toMatch(/OK .* read \d\d:\d\d:\d\d/);
  });
  it("an UNKNOWN side carries its reason", () => {
    const s = coverageSide({ health: "unknown", reason: "the NVR is slow", read_at: iso(5) });
    expect(s.text).toContain("the NVR is slow");
    expect(s.health).toBe("unknown");
  });
  it("a rule line names its direction, check status and state", () => {
    const t = ruleLine(rule());
    expect(t).toContain("person entering the white gate");
    expect(t).toContain("enter");
    expect(t).toContain("needs the owner check");
    expect(t).toContain("Unknown");
    expect(ruleLine(rule({ direction: "both" }))).toContain("both ways");
    expect(ruleLine(rule({ needs_check: false }))).not.toContain("needs the owner check");
  });
  it("overlay shapes scale to a 100 box and mark UNKNOWN rules dashed", () => {
    const zone = rule({ label: "garage", test: "zone", line: null, direction: null, health: "ok",
      reason: null, zone: [[0.35, 0.25], [0.8, 0.25], [0.8, 0.95], [0.35, 0.95]] });
    const shapes = overlayShapes([rule(), zone]);
    expect(shapes).toHaveLength(2);
    expect(shapes[0]).toMatchObject({ kind: "line", unknown: true, points: "62,44 78,44" });
    expect(shapes[1]).toMatchObject({ kind: "zone", unknown: false,
      points: "35,25 80,25 80,95 35,95" });
  });
  it("a rule with no geometry draws nothing", () => {
    expect(overlayShapes([rule({ test: "anywhere", line: null, zone: null })])).toEqual([]);
  });
  it("a near rule draws its vehicle zone", () => {
    const near = rule({ test: "near", line: null, direction: null,
      zone: [[0.1, 0.5], [0.65, 0.5], [0.65, 0.95], [0.1, 0.95]], near: 0.08 });
    expect(overlayShapes([near])[0]?.kind).toBe("zone");
  });
});

describe("coverage panel", () => {
  it("shows twelve rows, recorder against ATLAS, with read times", () => {
    const html = renderToStaticMarkup(createElement(CoveragePanel, { coverage: coverage() }));
    expect((html.match(/data-camera="/g) ?? []).length).toBe(12);
    expect(html).toContain("Recorder");
    expect(html).toContain("ATLAS");
    expect(html).toMatch(/read \d\d:\d\d:\d\d/);
    expect(html).toContain("needs the owner check");
  });
  it("an UNKNOWN row is hatched, dashed and says Unknown with the reason", () => {
    const cov = coverage({ cameras: [covRow(1, {
      atlas: { health: "unknown", reason: "no frame read recently", read_at: null } }),
      ...Array.from({ length: 11 }, (_, i) => covRow(i + 2))] });
    const html = renderToStaticMarkup(createElement(CoveragePanel, { coverage: cov }));
    const row = html.slice(html.indexOf('data-camera="1"'), html.indexOf('data-camera="2"'));
    expect(row).toContain("cam-unknown hatch-edge");
    expect(row).toContain("Unknown");
    expect(row).toContain("no frame read recently");
  });
  it("no data at all is twelve UNKNOWN rows with the word", () => {
    const html = renderToStaticMarkup(createElement(CoveragePanel, { coverage: null }));
    expect((html.match(/data-camera="/g) ?? []).length).toBe(12);
    expect((html.match(/cam-unknown hatch-edge/g) ?? []).length).toBeGreaterThanOrEqual(12);
    expect(html).toContain("Coverage not read");
  });
  it("shows the Tier 2 suggestion text and the busy-frame count when present", () => {
    const cov = coverage();
    cov.cameras[0] = covRow(1, { busy_frames: 7, suggestion: "Tier 2 suggestion: raise it." });
    const html = renderToStaticMarkup(createElement(CoveragePanel, { coverage: cov }));
    expect(html).toContain("Tier 2 suggestion: raise it.");
    expect(html).toContain("7 busy frames");
  });
});

describe("rule overlay", () => {
  it("draws every zone and line and lists each with the check note", () => {
    const zone = rule({ label: "garage door", test: "zone", line: null, direction: null,
      zone: [[0.35, 0.25], [0.8, 0.25], [0.8, 0.95], [0.35, 0.95]] });
    const html = renderToStaticMarkup(createElement(RuleOverlay, { rules: [rule(), zone] }));
    expect(html).toContain("<polyline");
    expect(html).toContain("<polygon");
    expect(html).toContain("garage door");
    expect((html.match(/needs the owner check/g) ?? []).length).toBeGreaterThanOrEqual(2);
  });
  it("without rules it draws nothing", () => {
    expect(renderToStaticMarkup(createElement(RuleOverlay, { rules: [] }))).toBe("");
  });
});

describe("metrics strip", () => {
  const ov = (): CamerasOverview => overview();
  it("failed reads are three UNKNOWN cells with no figure, never 0", () => {
    const cells = cameraMetrics(null, null, NOW);
    expect(cells.map((c) => c.key)).toEqual(["online", "nvr", "rules"]);
    expect(cells.every((c) => !c.read && c.figure === "Unknown" && c.readAt === null)).toBe(true);
    const html = renderToStaticMarkup(createElement(CamerasHead, {
      overview: null, coverage: null, now: NOW, error: "boom" }));
    expect(html).toContain("Unknown");
    expect(html).toContain("not read");
    expect(html).not.toMatch(/\b0 of 12\b|>0</);
    expect((html.match(/data-health="unknown"/g) ?? []).length).toBeGreaterThanOrEqual(3);
  });
  it("online counts only cameras read online with a fresh frame, of twelve", () => {
    const cams = Array.from({ length: 12 }, (_, i) => card(i + 1));
    cams[0] = card(1, { online: false, health: "failing" });
    cams[1] = card(2, { frame_at: iso(600) });             // stale frame: not counted
    const cells = cameraMetrics(overview({ cameras: cams }), null, NOW);
    expect(cells[0]).toMatchObject({ read: true, figure: "10 of 12" });
    expect(cells[0]!.readAt).not.toBeNull();
  });
  it("a real zero online is a read figure with its time", () => {
    const cams = Array.from({ length: 12 }, (_, i) => card(i + 1, { online: false, health: "failing" }));
    const cells = cameraMetrics(overview({ cameras: cams }), null, NOW);
    expect(cells[0]).toMatchObject({ read: true, figure: "0 of 12" });
  });
  it("NVR cell uses the NVR word and read time; unread NVR is UNKNOWN", () => {
    expect(cameraMetrics(ov(), null, NOW)[1]).toMatchObject({ read: true, figure: "OK" });
    const bad = overview({ nvr: { health: "ok", reason: null, read_at: null } });
    expect(cameraMetrics(bad, null, NOW)[1]).toMatchObject({ read: false, figure: "Unknown" });
  });
  it("rules cell is the rule count from coverage, UNKNOWN when coverage is unread", () => {
    expect(cameraMetrics(ov(), null, NOW)[2]).toMatchObject({ read: false, figure: "Unknown" });
    const cells = cameraMetrics(ov(), coverage(), NOW);
    expect(cells[2]).toMatchObject({ read: true, figure: "12" });
    expect(cells[2]!.readAt).not.toBeNull();
  });
});

describe("page chrome", () => {
  it("tab strip is a tablist with one selected tab", () => {
    const html = renderToStaticMarkup(createElement(CamerasTabs, { tab: "clips", onTab: () => {} }));
    expect(html).toContain('role="tablist"');
    expect((html.match(/aria-selected="true"/g) ?? []).length).toBe(1);
    for (const t of ["Feeds", "Clips", "Coverage", "People"]) expect(html).toContain(t);
  });
  it("the page never shows motion events, WhatsApp alerts or an events-today metric", () => {
    const html = renderToStaticMarkup(createElement(Cameras));
    for (const bad of ["Motion events", "WhatsApp", "Events today"]) expect(html).not.toContain(bad);
    expect(html).toContain('role="tablist"');
  });
});
