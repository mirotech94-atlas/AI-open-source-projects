// Air Glass inside ATLAS: wires camera (or the demo hand) -> gestures ->
// scene + overlay, and the page's ears -> what the hand is doing -> ATLAS.
// The React page (pages/AirGlass.tsx) renders the markup and the
// low-frequency state this hands it through `onUi`; the per-frame work
// (hands, cursor, rings, the hint line, the kinetics, the tray's height) is
// done here imperatively, so nothing re-renders sixty times a second.
//
// One gesture: pinch and slide, either hand (gestures.ts). Voice works with
// it: "make this bigger", "put it here", "ATLAS, look" (talk.ts), and "show
// me a car" searches the free library shelf and brings the best match out
// (library.ts; the bytes come through ATLAS, never from the sources directly).
//
// Privacy. Every frame is processed in this tab; no frame or landmark is
// sent anywhere -- except ONE still, captured only when he asks ATLAS to
// look (voice, the Ask ATLAS button, or the ATLAS Menu's ⌘ conversation),
// sent to /api/airglass/look, and kept nowhere. "ATLAS is looking…" flashes
// whenever that still is taken. Speech: on-device recognition when the
// browser promises it; otherwise only stretches of speech go to ATLAS's own
// transcriber (voice.ts), and the mic badge says which. ATLAS answers in its
// Fish Audio voice (/api/voice/speak), never the browser's.
import { Camera, type CameraStatus, type ModelStatus } from "./camera";
import { explainAdded, explainProduct, MODEL_ABOUT, PRODUCTS, type Source } from "./catalog";
import { explainPanel, PANELS, readPanel, type PanelKind } from "./data";
import { drawCardFace, drawPanel } from "./panelCanvas";
import { COACH_KEY, coachAdvance, coachInitial, type CoachEvent, type CoachState } from "./coach";
import { demoFrame, DEMO_LENGTH } from "./demo";
import {
  clamp, Follow, GestureEngine, HandTracker, hintFor, hitBoxes, releaseMotion, smoothstep,
  stepFlight, TUNING, videoToScreen, type EngineEvent, type EngineOut, type ModelDisc, type Pt,
  type TargetBox, type TargetKind,
} from "./gestures";
import { classifyHealth, readHealth, POLL_MS, type HealthState } from "./health";
import { Overlay } from "./hud";
import { easeToward, layoutFor, VIDEO_DEFAULT, type Layout } from "./layout";
import {
  explainLibrary, fetchModelFiles, searchLibrary, sourcesDown, type LibraryModel,
} from "./library";
import * as SceneModule from "./scene";
import { captureStill } from "./still";
import {
  GestureHistory, interpret, utteranceEndingNow, type GestureContext, type Intent, type Named,
  type Snap, type Utterance,
} from "./talk";
import { FishSpeaker, Listener, type ListenMode } from "./voice";
import { OfflinePack, unreachable } from "./offlinePack";

type Scene = typeof SceneModule;
type Object3D = import("three").Object3D;

export interface AirGlassEls {
  root: HTMLElement;
  stage: HTMLElement;
  video: HTMLVideoElement;
  gl: HTMLCanvasElement;
  hands: HTMLCanvasElement;
  hint: HTMLElement;
  kinetics: HTMLElement;
  meter: HTMLElement;
}

export interface UiState {
  source: "camera" | "demo" | "none";
  camera: CameraStatus;
  cameraLive: boolean;
  model: ModelStatus;
  delegate: string | null;
  current: string | null;          // "tile:<id>" on show
  custom: Array<{ id: string; name: string }>;
  vault: Array<{ name: string; at: Date }>;
  hologram: boolean;
  voiceOn: boolean;
  /** The page's ears: on-device, server (stretches of speech to ATLAS's
   * transcriber), muted, or why not. */
  listen: ListenMode;
  listenDetail: string;
  /** What was last heard, and what Air Glass made of it. */
  heard: string;
  speaking: boolean;
  /** A still is being taken and shown to ATLAS. */
  looking: boolean;
  /** ATLAS's last answer, shown beside the object. */
  reply: { text: string; who: string; n: number } | null;
  health: { state: HealthState | "OFF"; detail: string; at: Date | null };
  caption: { who: string; text: string; n: number } | null;
  coach: CoachState;
  demoCaption: string | null;
  message: { title: string; body: string; kind: "camera" | "model" | "webgl" } | null;
  dragging: boolean;
  /** Which shelf the tray shows. */
  tab: Source;
  handsOn: boolean;
  keysOn: boolean;
  spinning: boolean;
  /** The free library shelf: the last search and what came of it. */
  library: LibraryUi;
  /** Whose model is on show, when it came from the free library. */
  credit: { name: string; text: string; license: string; required: boolean } | null;
}

export interface LibraryUi {
  query: string;
  state: "idle" | "searching" | "ok" | "error";
  /** Why the search failed, in the server's words. */
  detail: string;
  models: LibraryModel[];
  /** A source that did not answer while another did. */
  down: string;
  readAt: Date | null;
  /** The model whose files are being fetched. */
  loading: string | null;
}

const LIBRARY_IDLE: LibraryUi = {
  query: "", state: "idle", detail: "", models: [], down: "", readAt: null, loading: null,
};

interface Entry {
  key: string; name: string; line: string; object: Object3D;
  /** Fixed or freshly read words; never invented. */
  explain: () => string | Promise<string>;
  /** Fixed words about it, for a question to ATLAS (never a figure). */
  about: string;
  spin: boolean;
  /** Starting size: a panel's text needs to be larger than a model. */
  scale?: number;
  /** Stop whatever keeps this entry fresh (a data panel's re-read). */
  stop?: () => void;
}

const TABS: readonly Source[] = ["model", "data", "academy", "library"];
const SPIN_RATE = { slow: 0.15, normal: 0.35, fast: 1.2 } as const;

const readStore = (k: string): string | null => {
  try { return window.localStorage.getItem(k); } catch { return null; }
};
const writeStore = (k: string, v: string): void => {
  try { window.localStorage.setItem(k, v); } catch { /* private window: the coach shows again */ }
};
const prefersReducedMotion = (): boolean => {
  try { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch { return false; }
};
const nowS = (): number => performance.now() / 1000;

export class AirGlass {
  ui: UiState;
  private scene: Scene | null = null;
  private stage: InstanceType<Scene["Stage"]> | null = null;
  private readonly overlay: Overlay;
  private readonly camera: Camera;
  private readonly speaker = new FishSpeaker();
  private readonly pack = new OfflinePack();
  private readonly listener: Listener;
  private readonly tracker = new HandTracker();
  private readonly engine = new GestureEngine();
  private readonly follow = new Follow();
  private readonly history = new GestureHistory();
  private readonly reduced = prefersReducedMotion();
  private readonly custom: Array<{ id: string; name: string; object: Object3D }> = [];
  /** Free-library models already fetched and parsed, by library id. */
  private readonly libLoaded = new Map<string, { model: LibraryModel; object: Object3D }>();
  private libSeq = 0;
  private readonly vault: Array<Entry & { at: Date }> = [];
  private currentEntry: Entry | null = null;
  private holding = false;
  private spawn: { t0: number; scale: number } | null = null;
  private flight: { vx: number; vy: number; spin: number; damping: number; toZone: boolean } | null = null;
  private tween: { from: Pt; to: Pt; t0: number; dur: number } | null = null;
  private lastOut: EngineOut | null = null;
  private raf = 0;
  private destroyed = false;
  private lastVideoTime = -1;
  private demoClock = 0;
  private demoLoop = -1;
  private demoTile: string | null = null;
  private healthTimer: number | null = null;
  private resizeObs: ResizeObserver | null = null;
  private hovered: HTMLElement | null = null;
  private zoneEl: HTMLElement | null = null;
  private replyEl: HTMLElement | null = null;
  private boxes: TargetBox[] = [];
  private boxesAt = 0;
  private layout: Layout | null = null;
  private trayY = -1;
  private lowest: Array<{ t: number; y: number }> = [];
  private fps = 0;
  private lastFrame = performance.now();
  private lastKinetics = 0;
  private lastHint = "";
  private captionN = 0;
  private replyN = 0;
  private dragDepth = 0;
  private messageDismissed = false;
  private readonly offs: Array<() => void> = [];

  constructor(private readonly els: AirGlassEls,
              private readonly onUi: (ui: UiState) => void,
              opts: { demo?: boolean } = {}) {
    this.overlay = new Overlay(els.hands);
    this.camera = new Camera(els.video, () => this.syncCamera());
    this.listener = new Listener(
      (u) => { this.hearUtterance(u); },
      (mode, detail) => this.set({ listen: mode, listenDetail: detail }),
      () => (this.speaker.speaking ? Infinity : this.speaker.quietUntil),
    );
    this.speaker.onChange = (speaking): void => this.set({ speaking });
    // ATLAS's offline lines in its Fish voice: the copy kept from an earlier
    // visit first, then a fresh one while ATLAS is reachable.
    this.speaker.pack = this.pack;
    void this.pack.load().then(() => this.pack.refresh());
    const stored = readStore(COACH_KEY);
    this.ui = {
      source: "none", camera: "off", cameraLive: false, model: "loading", delegate: null,
      current: null, custom: [], vault: [], hologram: true, voiceOn: true,
      listen: "off", listenDetail: "", heard: "", speaking: false, looking: false, reply: null,
      health: { state: "OFF", detail: "", at: null }, caption: null,
      coach: coachInitial(stored), demoCaption: null, message: null, dragging: false,
      tab: "model", handsOn: true, keysOn: true, spinning: !this.reduced,
      library: LIBRARY_IDLE, credit: null,
    };
    const w = window as unknown as { __airglass?: unknown; __airglassCommand?: unknown };
    w.__airglass = this.inspect();
    // The ATLAS Menu window's ⌘ conversation: words heard on the Mac are
    // offered here first, understood with what the hand is doing. True when
    // Air Glass took them (and answers); false hands them back to the Menu.
    w.__airglassCommand = (text: unknown): boolean => typeof text === "string" && this.hearMenu(text);
    this.listen();
    void this.boot(opts.demo === true);
  }

  // ------------------------------------------------------------------ setup

  private async boot(demo: boolean): Promise<void> {
    this.scene = SceneModule;
    try {
      this.stage = new this.scene.Stage(this.els.gl);
      if (this.reduced) this.stage.autoSpin = false;
    } catch (e) {
      this.set({ message: { kind: "webgl", title: "WebGL unavailable",
        body: `The hologram cannot be drawn in this browser (${String((e as Error)?.message ?? e)}).` } });
    }
    this.measure();
    this.resizeObs = new ResizeObserver(() => this.measure());
    this.resizeObs.observe(this.els.stage);
    this.show(this.entryFor("tile:globe"), null);
    this.raf = requestAnimationFrame(this.frame);
    void this.listener.start();
    // The model loads whatever happens to the camera, so a page with no
    // camera (or in demo) still has it ready the moment one appears.
    const model = this.camera.loadModel();
    if (demo) {
      this.startDemo();
    } else {
      const ok = await this.camera.open();
      await model;
      if (this.destroyed) return;
      if (ok && this.camera.model !== "ready") this.camera.stopTracks();
      this.syncCamera();
    }
  }

  private videoSize(): { w: number; h: number } {
    const v = this.els.video;
    return v.videoWidth && v.videoHeight ? { w: v.videoWidth, h: v.videoHeight } : VIDEO_DEFAULT;
  }

  private measure(): void {
    const r = this.els.stage.getBoundingClientRect();
    this.stage?.resize(r.width, r.height);
    this.overlay.resize(r.width, r.height);
    this.zoneEl = this.els.root.querySelector<HTMLElement>("[data-target='zone:atlas']");
    this.replyEl = this.els.root.querySelector<HTMLElement>(".ag-reply");
    this.relayout(0, true);
  }

  /** Place the tray, the button column and the zone where a raised hand can
   * reach them (layout.ts), and ease the tray toward where it should be for
   * the hand seen now. */
  private relayout(dt: number, snap = false): void {
    const view = this.stage?.view ?? { w: this.els.stage.clientWidth, h: this.els.stage.clientHeight };
    if (view.w < 2 || view.h < 2) return;
    const tiles = this.els.root.querySelectorAll(".ag-tray > .ag-tile").length || 4;
    const hands = this.tracker.hands;
    const palm = hands.length ? Math.max(...hands.map((h) => h.size)) : null;
    const L = layoutFor(view, this.videoSize(), tiles, palm);
    let target = L.tray.y;
    // A hand that has been up a while but has not come near the tray: bring
    // the tray up to it (never above the middle: the model lives there).
    const t = nowS();
    this.lowest = this.lowest.filter((s) => t - s.t < 3);
    const low = this.lowest.length > 40 ? Math.max(...this.lowest.map((s) => s.y)) : null;
    if (low !== null && low < target - 40 && (this.lastOut?.phase ?? "IDLE") === "IDLE") {
      target = Math.min(L.tray.y, Math.max(view.h * 0.5, low + 10));
    }
    this.trayY = snap || this.trayY < 0 ? target : easeToward(this.trayY, target, dt, this.reduced ? 50 : 2);
    this.layout = L;
    const s = this.els.root.style;
    s.setProperty("--ag-tile-w", `${L.tile.w}px`);
    s.setProperty("--ag-tile-h", `${L.tile.h}px`);
    s.setProperty("--ag-tray-top", `${Math.round(this.trayY)}px`);
    s.setProperty("--ag-side-left", `${Math.round(L.side.x)}px`);
    s.setProperty("--ag-side-top", `${Math.round(L.side.y)}px`);
    s.setProperty("--ag-zone-left", `${Math.round(L.zone.x)}px`);
    s.setProperty("--ag-zone-top", `${Math.round(L.zone.y)}px`);
  }

  /** Cache every pinchable target's box, relative to the stage. */
  private readBoxes(): void {
    const s = this.els.stage.getBoundingClientRect();
    this.boxes = [...this.els.root.querySelectorAll<HTMLElement>("[data-target]")].flatMap((el) => {
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) return [];
      const kind = (el.dataset.kind ?? "button") as TargetKind;
      return [{ id: el.dataset.target ?? "", kind, rect: { x: r.left - s.left, y: r.top - s.top, w: r.width, h: r.height } }];
    });
  }

  private listen(): void {
    const on = <K extends keyof HTMLElementEventMap>(el: HTMLElement | Window, type: K,
      fn: (e: HTMLElementEventMap[K]) => void): void => {
      el.addEventListener(type, fn as EventListener);
      this.offs.push(() => el.removeEventListener(type, fn as EventListener));
    };
    on(window, "keydown", (e) => this.onKey(e));
    // Drops land on this page only: a handler on window would hijack drops
    // on every other screen after a visit here.
    const root = this.els.root;
    on(root, "dragenter", (e) => {
      e.preventDefault();
      this.dragDepth += 1;
      if (!this.ui.dragging) this.set({ dragging: true });
    });
    on(root, "dragleave", () => {
      this.dragDepth -= 1;
      if (this.dragDepth <= 0) { this.dragDepth = 0; this.set({ dragging: false }); }
    });
    on(root, "dragover", (e) => e.preventDefault());
    on(root, "drop", (e) => {
      e.preventDefault();
      this.dragDepth = 0;
      this.set({ dragging: false });
      const files = e.dataTransfer?.files;
      if (files?.length) void this.openFiles(files);
    });
  }

  /** Keys: 1-9 bring out, arrows turn, + - size, R reset, A give, E explain,
   * L look, M mute, D demo, Esc closes the coach or the message. */
  private onKey(e: KeyboardEvent): void {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const t = e.target as HTMLElement | null;
    if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    if (e.key === "Escape") { this.dismissCoach(); this.dismissMessage(); return; }
    if (!this.ui.keysOn) return;
    const k = e.key.toLowerCase();
    const step = Math.PI / 12;
    const act: Record<string, () => void> = {
      r: () => this.reset(), a: () => this.give(), e: () => void this.explain(),
      d: () => this.toggleDemo(), l: () => void this.look("What am I holding?"),
      m: () => void this.toggleListen(),
      "+": () => this.resize(1.25), "=": () => this.resize(1.25), "-": () => this.resize(0.8),
      arrowleft: () => this.stage?.roll(0, -step), arrowright: () => this.stage?.roll(0, step),
      arrowup: () => this.stage?.roll(-step, 0), arrowdown: () => this.stage?.roll(step, 0),
    };
    const digit = /^[1-9]$/.test(k) ? Number(k) - 1 : -1;
    const fn = digit >= 0 ? (): void => {
      const key = this.tileKeys(this.ui.tab)[digit];
      if (key) this.pick(key);
    } : act[k];
    if (fn) { fn(); e.preventDefault(); }
  }

  private set(patch: Partial<UiState>): void {
    if (this.destroyed) return;
    this.ui = { ...this.ui, ...patch };
    this.onUi(this.ui);
  }

  private inspect(): object {
    const self = this;
    return {
      get source() { return self.ui.source; },
      get phase() { return self.lastOut?.phase ?? "IDLE"; },
      get hands() { return self.tracker.hands.length; },
      get current() { return self.currentEntry?.name ?? null; },
      get vault() { return self.vault.map((v) => v.name); },
      get scale() { return self.stage?.holder.scale.x ?? null; },
      get model() { return self.camera.model; },
      get camera() { return self.camera.camera; },
      get coach() { return self.ui.coach; },
      get listen() { return self.ui.listen; },
      get layout() { return self.layout; },
      get trayY() { return self.trayY; },
      get library() { return self.ui.library; },
      /** For checks: what an utterance would mean right now. */
      interpret: (text: string): Intent => interpret(utteranceEndingNow(text, nowS()), self.history, self.names()),
    };
  }

  private syncCamera(): void {
    const c = this.camera;
    const demo = this.ui.source === "demo";
    const source: UiState["source"] = demo ? "demo"
      : c.live && c.model === "ready" ? "camera" : "none";
    let message: UiState["message"] = this.ui.message?.kind === "webgl" ? this.ui.message : null;
    if (!demo && !message && !this.messageDismissed) {
      if (c.model === "missing") {
        message = { kind: "model", title: "The hand model is not built into this ATLAS",
          body: "/wake/hand_landmarker.task is missing. Run ui/scripts/fetch-hand-model.sh "
            + "before the UI build to include it. Demo mode works without it." };
      } else if (c.model === "failed") {
        message = { kind: "model", title: "The hand model could not load",
          body: "The same-origin model files under /wake did not start. Demo mode still works." };
      } else if (c.camera === "unavailable") {
        message = { kind: "camera", title: "Camera unavailable: allow camera access or use the demo",
          body: `Air Glass needs the camera (${c.cameraError ?? "no camera"}). Nothing is recorded.` };
      }
    }
    this.set({ source, camera: c.camera, cameraLive: c.live, model: c.model,
      delegate: c.delegate, message });
    this.relayout(0, true);
  }

  // ----------------------------------------------------------------- models

  /**
   * What a tile key shows. "tile:<id>" a built-in or added model,
   * "data:<kind>" an ATLAS data panel, "shop:<slug>" an Academy card,
   * "lib:<source>:<slug>" a free-library model already fetched.
   */
  private entryFor(key: string): Entry | null {
    const sc = this.scene;
    if (!sc) return null;
    const cut = key.indexOf(":");
    const kind = cut < 0 ? key : key.slice(0, cut);
    const id = cut < 0 ? "" : key.slice(cut + 1);
    if (kind === "lib") {
      const got = this.libLoaded.get(id);
      if (!got) return null;
      const m = got.model;
      const about = explainLibrary(m);
      return { key, name: m.name, line: `${m.name}, received.`, object: sc.cloneProp(got.object),
        explain: () => about, about, spin: true };
    }
    if (kind === "tile") {
      const p = sc.PROPS.find((x) => x.id === id);
      if (p) {
        const about = MODEL_ABOUT[p.id] ?? p.name;
        return { key, name: p.name, line: p.line, object: p.make(), explain: () => about, about, spin: true };
      }
      const c = this.custom.find((x) => x.id === id);
      if (c) {
        return { key, name: c.name, line: "Caught it.", object: sc.cloneProp(c.object),
          explain: () => explainAdded(c.name), about: explainAdded(c.name), spin: true };
      }
    } else if (kind === "data") {
      const spec = PANELS.find((x) => x.kind === id);
      if (spec) return this.panelEntry(spec.kind, spec.name);
    } else if (kind === "shop") {
      const p = PRODUCTS.find((x) => x.slug === id);
      if (p) {
        let front: { url: string } | { canvas: HTMLCanvasElement };
        if (p.cover) {
          front = { url: p.cover };
        } else {
          const canvas = document.createElement("canvas");
          drawCardFace(canvas, p.title, p.kind);
          front = { canvas };
        }
        return { key, name: p.title, line: `${p.title}, received.`, object: sc.makeCard(front),
          explain: () => explainProduct(p), about: explainProduct(p), spin: true, scale: 1.4 };
      }
    }
    return null;
  }

  /** A data panel: read now, re-read every minute while on show, and read
   * again (fresh) whenever it is explained. */
  private panelEntry(kind: PanelKind, name: string): Entry | null {
    const sc = this.scene;
    if (!sc) return null;
    const canvas = document.createElement("canvas");
    drawPanel(canvas, null);
    const panel = sc.makePanel(canvas);
    let alive = true;
    const refresh = async (): Promise<ReturnType<typeof explainPanel>> => {
      const data = await readPanel(kind);
      if (alive && !this.destroyed) { drawPanel(canvas, data); panel.redraw(); }
      return explainPanel(data);
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    return {
      key: `data:${kind}`, name, line: `${name}, filed.`, object: panel.object,
      explain: refresh, about: `${name}: a live ATLAS data panel, read from the dashboard.`,
      spin: false, scale: 1.7,
      stop: () => { alive = false; window.clearInterval(timer); },
    };
  }

  /** The keys on a shelf, in tray order (1-9 pick from these). */
  tileKeys(tab: Source | "all" = "all"): string[] {
    const models = [...(this.scene?.PROPS ?? []).map((p) => `tile:${p.id}`), ...this.custom.map((c) => `tile:${c.id}`)];
    const data = PANELS.map((p) => `data:${p.kind}`);
    const shop = PRODUCTS.map((p) => `shop:${p.slug}`);
    if (tab === "model") return models;
    if (tab === "data") return data;
    if (tab === "academy") return shop;
    if (tab === "library") return this.ui.library.models.map((m) => `lib:${m.id}`);
    return [...models, ...data, ...shop];
  }

  /** Everything that can be picked by saying its name. */
  private names(): Named[] {
    const out: Named[] = [];
    for (const p of this.scene?.PROPS ?? []) out.push({ key: `tile:${p.id}`, name: p.name });
    out.push({ key: "tile:knot", name: "knot" }, { key: "tile:atlas", name: "mark" });
    for (const c of this.custom) out.push({ key: `tile:${c.id}`, name: c.name });
    for (const p of PANELS) out.push({ key: `data:${p.kind}`, name: p.name });
    out.push({ key: "data:tasks", name: "tasks" }, { key: "data:sales", name: "sales" },
      { key: "data:agents", name: "agents" });
    for (const p of PRODUCTS) out.push({ key: `shop:${p.slug}`, name: p.title });
    // The library shelf's results and what was fetched from it: names the
    // sources gave, matched as words only (talk.ts strips everything else).
    for (const m of this.ui.library.models) out.push({ key: `lib:${m.id}`, name: m.name });
    for (const [id, { model }] of this.libLoaded) out.push({ key: `lib:${id}`, name: model.name });
    return out;
  }

  private nameOf(key: string): string {
    return this.names().find((n) => n.key === key)?.name
      ?? this.els.root.querySelector<HTMLElement>(`[data-target='${key}']`)?.dataset.name ?? key;
  }

  setTab(tab: Source): void {
    if (!TABS.includes(tab)) return;
    this.set({ tab });
    // The first visit to the library shelf lists what it has, read now.
    if (tab === "library" && this.ui.library.state === "idle") void this.searchLibrary("");
  }

  /**
   * Search the free library (through ATLAS). `bring`: bring the best match
   * out, and say so -- the voice path ("show me a car"). A search that could
   * not run clears the shelf and says why: old results are never shown as
   * if they were the answer.
   */
  async searchLibrary(query: string, bring = false): Promise<void> {
    const q = query.trim().slice(0, 80);
    const n = ++this.libSeq;
    this.set({ tab: "library",
      library: { ...this.ui.library, query: q, state: "searching", detail: "", down: "" } });
    try {
      const res = await searchLibrary(q);
      if (this.destroyed || n !== this.libSeq) return;
      const down = sourcesDown(res);
      this.set({ library: { query: q, state: "ok", detail: "", models: res.models, down,
        readAt: res.readAt, loading: null } });
      if (!bring) return;
      const first = res.models[0];
      if (!first) {
        this.answer(down ? `I found nothing for “${q}”, and ${down}.`
          : `The free library has nothing for “${q}”.`);
        return;
      }
      await this.loadLibrary(first.id);
    } catch (e) {
      if (this.destroyed || n !== this.libSeq) return;
      const detail = (e as Error)?.message ?? String(e);
      this.set({ library: { ...LIBRARY_IDLE, query: q, state: "error", detail } });
      if (bring) this.answer(`I couldn't search the free library: ${detail}.`);
    }
  }

  /** Fetch one library model's files through ATLAS, parse them like a model
   * from disk, and bring it out. One at a time. */
  async loadLibrary(id: string): Promise<void> {
    const key = `lib:${id}`;
    if (this.libLoaded.has(id)) { this.pick(key); return; }
    const m = this.ui.library.models.find((x) => x.id === id);
    const sc = this.scene;
    if (!m || !sc || this.ui.library.loading) return;
    this.set({ library: { ...this.ui.library, loading: id } });
    this.caption(`Fetching ${m.name} from ${m.source}…`, "LIBRARY");
    try {
      const files = await fetchModelFiles(m);
      const { object } = await sc.parseModel(files, m.main);
      if (this.destroyed) return;
      this.libLoaded.set(id, { model: m, object });
      if (!this.stage) {
        this.answer(`I fetched ${m.name} from ${m.source}, but this browser cannot draw it.`);
        return;
      }
      this.show(this.entryFor(key), null);
      this.coach("picked");
      this.answer(`${m.name}, from ${m.source}.`);
    } catch (e) {
      if (this.destroyed) return;
      this.caption(`Could not load ${m.name}: ${(e as Error)?.message ?? String(e)}`, "LIBRARY");
    } finally {
      if (!this.destroyed) this.set({ library: { ...this.ui.library, loading: null } });
    }
  }

  /** Show an entry at a stage point (null: the middle), growing in. */
  private show(entry: Entry | null, at: Pt | null): void {
    const st = this.stage;
    if (!st || !entry) return;
    this.flight = null;
    this.tween = null;
    this.holding = false;
    this.currentEntry?.stop?.();
    st.show(entry.object, at ? st.screenToWorld(at.x, at.y) : null);
    const scale = entry.scale ?? 1;
    st.holder.scale.setScalar(this.reduced ? scale : 0.001);
    this.spawn = this.reduced ? null : { t0: nowS(), scale };
    st.autoSpin = entry.spin && this.ui.spinning;
    st.showBase = entry.spin;
    this.currentEntry = entry;
    const tab: Source = entry.key.startsWith("data:") ? "data"
      : entry.key.startsWith("shop:") ? "academy"
        : entry.key.startsWith("lib:") ? "library" : "model";
    const lib = entry.key.startsWith("lib:") ? this.libLoaded.get(entry.key.slice(4))?.model : null;
    this.set({ current: entry.key, tab, credit: lib ? { name: lib.name, text: lib.attribution,
      license: lib.licenseName, required: lib.creditRequired } : null });
  }

  /** Keys and voice: bring a tile's model out to the middle. */
  pick(key: string): void {
    if (key === this.currentEntry?.key && this.stage?.current) return;
    if (key.startsWith("lib:") && !this.libLoaded.has(key.slice(4))) {
      void this.loadLibrary(key.slice(4));
      return;
    }
    const entry = this.entryFor(key);
    if (!entry) return;
    this.show(entry, null);
    this.coach("picked");
  }

  next(): void {
    const keys = this.tileKeys(this.ui.tab === "library" && !this.ui.library.models.length
      ? "model" : this.ui.tab);
    const i = keys.indexOf(this.currentEntry?.key ?? "");
    const k = keys[(i + 1) % keys.length];
    if (k) this.pick(k);
  }

  /** ATLAS says what it is holding: fixed words, or numbers read just now. */
  async explain(entry: Entry | null = this.currentEntry): Promise<void> {
    const e = entry;
    if (!e) {
      this.answer("Nothing is on show. Pinch a model in the tray and slide it up.", "AIR GLASS");
      return;
    }
    if (e.key.startsWith("data:")) this.caption(`Reading ${e.name}…`, "ATLAS");
    const words = await e.explain();
    if (this.destroyed) return;
    this.answer(words);
  }

  /** "spin" starts the idle turn (at a speed); "stop" holds it still. */
  setSpinning(on: boolean, speed: keyof typeof SPIN_RATE = "normal"): void {
    this.set({ spinning: on });
    const st = this.stage;
    if (!st) return;
    st.idleSpin = SPIN_RATE[speed];
    st.autoSpin = on;
    st.spin = { x: 0, y: on && !this.reduced ? st.idleSpin * 3 : 0 };
  }

  toggleHands(): void {
    if (this.ui.handsOn) {
      this.camera.stopTracks();
      this.tracker.reset();
      this.set({ handsOn: false, cameraLive: false,
        source: this.ui.source === "demo" ? "demo" : "none" });
    } else {
      this.set({ handsOn: true });
      if (this.ui.source !== "demo") void this.retryCamera();
    }
  }

  toggleKeys(): void { this.set({ keysOn: !this.ui.keysOn }); }

  /** Close the camera/model notice: buttons, keys and voice still work. */
  dismissMessage(): void {
    if (!this.ui.message || this.ui.message.kind === "webgl") return;
    this.messageDismissed = true;
    this.set({ message: null });
  }

  async openFiles(files: Iterable<File>): Promise<void> {
    if (!this.scene) return;
    try {
      this.caption("Loading the model from disk…", "FILE");
      const { name, object } = await this.scene.loadModelFiles(files);
      if (this.destroyed) return;
      const id = `file${this.custom.length + 1}`;
      this.custom.push({ id, name, object });
      this.set({ custom: this.custom.map(({ id: i, name: n }) => ({ id: i, name: n })) });
      this.show(this.entryFor(`tile:${id}`), null);
      this.caption(`${name} loaded. It never left this Mac.`, "FILE");
    } catch (e) {
      this.caption(`Could not load that file: ${(e as Error)?.message ?? String(e)}`, "FILE");
    }
  }

  resize(factor: number): void {
    const h = this.stage?.holder;
    if (!h || !this.stage?.current) return;
    this.spawn = null;
    h.scale.setScalar(clamp(h.scale.x * factor, TUNING.minScale, TUNING.maxScale));
  }

  /** Everything back as it was when the page opened. */
  reset(): void {
    this.flight = null;
    this.tween = null;
    this.holding = false;
    this.vault.length = 0;
    this.set({ vault: [], spinning: !this.reduced, tab: "model", reply: null });
    this.show(this.entryFor("tile:globe"), null);
    this.caption("Reset. Everything is back.", "AIR GLASS");
  }

  /** Give the model to ATLAS: it flies into the zone, then ATLAS takes it. */
  give(): void {
    const st = this.stage;
    if (!st?.current || !this.zoneEl) return;
    const z = this.zoneRect();
    if (!z) return;
    if (this.reduced) { this.caught(); return; }
    const target = st.screenToWorld(z.x + z.w / 2, z.y + z.h / 2);
    const d = 2.2;
    this.holding = false;
    this.tween = null;
    this.flight = {
      vx: (target.x - st.holder.position.x) * d, vy: (target.y - st.holder.position.y) * d,
      spin: 6, damping: TUNING.glideDamping * 0.4, toZone: true,
    };
  }

  /** "Put it here": the model goes to a stage point. */
  moveTo(p: Pt): void {
    const st = this.stage;
    if (!st?.current) return;
    const from = st.worldToScreen(st.holder.position);
    this.flight = null;
    this.tween = { from, to: { x: p.x, y: p.y }, t0: nowS(), dur: this.reduced ? 0 : 0.35 };
  }

  private caught(): void {
    const entry = this.currentEntry;
    this.flight = null;
    this.tween = null;
    this.holding = false;
    entry?.stop?.();
    this.stage?.clear();
    this.currentEntry = null;
    if (!entry) return;
    this.vault.unshift({ ...entry, at: new Date() });
    this.set({ current: null, credit: null,
      vault: this.vault.map((v) => ({ name: v.name, at: v.at })) });
    this.zoneEl?.classList.remove("ag-catch");
    void this.zoneEl?.offsetWidth;
    this.zoneEl?.classList.add("ag-catch");
    this.answer(entry.line);
    this.coach("given");
  }

  // ---------------------------------------------------------------- toggles

  toggleLook(): void {
    const on = !this.ui.hologram;
    this.stage?.setHologram(on);
    this.set({ hologram: on });
  }

  toggleVoice(): void {
    this.speaker.enabled = !this.ui.voiceOn;
    if (!this.speaker.enabled) this.speaker.stop();
    this.set({ voiceOn: this.speaker.enabled });
  }

  /** The Mic button: mute closes the microphone; unmute opens it again. */
  async toggleListen(): Promise<void> {
    const m = this.ui.listen;
    if (m === "on-device" || m === "server" || m === "starting") {
      this.listener.stop("muted");
      return;
    }
    await this.listener.start();
  }

  toggleStatus(): void {
    if (this.healthTimer === null) {
      this.healthTimer = window.setInterval(() => void this.pollHealth(), POLL_MS);
      this.set({ health: { state: "UNKNOWN", detail: "reading…", at: null } });
      void this.pollHealth();
    } else {
      window.clearInterval(this.healthTimer);
      this.healthTimer = null;
      this.set({ health: { state: "OFF", detail: "", at: null } });
    }
  }

  private async pollHealth(): Promise<void> {
    const at = new Date();
    const verdict = classifyHealth(await readHealth());
    if (this.healthTimer === null || this.destroyed) return;
    this.set({ health: { ...verdict, at } });
  }

  toggleDemo(): void {
    if (this.ui.source === "demo") this.stopDemo(); else this.startDemo();
  }

  private startDemo(): void {
    this.camera.stopTracks();   // no camera light while a synthetic hand plays
    this.demoClock = 0;         // the demo's own clock, in seconds of frames played
    this.demoLoop = -1;
    this.tracker.reset();
    this.set({ source: "demo", cameraLive: false, message: this.ui.message?.kind === "webgl" ? this.ui.message : null });
  }

  private stopDemo(): void {
    this.tracker.reset();
    this.set({ source: "none", demoCaption: null });
    void this.retryCamera();
  }

  async retryCamera(): Promise<void> {
    this.messageDismissed = false;
    this.set({ message: null, source: "none", handsOn: true });
    void this.camera.loadModel();
    const ok = await this.camera.open();
    if (this.destroyed) return;
    if (ok && this.camera.model !== "ready") this.camera.stopTracks();
    this.syncCamera();
  }

  dismissCoach(): void {
    if (this.ui.coach.dismissed) return;
    writeStore(COACH_KEY, "done");
    this.set({ coach: { ...this.ui.coach, dismissed: true } });
  }

  private coach(e: CoachEvent): void {
    const next = coachAdvance(this.ui.coach, e);
    if (next === this.ui.coach) return;
    if (next.dismissed && !this.ui.coach.dismissed && this.ui.source === "camera") {
      writeStore(COACH_KEY, "done");
    }
    this.set({ coach: next });
  }

  private caption(text: string, who = "ATLAS"): void {
    this.captionN += 1;
    this.set({ caption: { who, text, n: this.captionN } });
  }

  /** ATLAS's answer: beside the object, and in ATLAS's Fish Audio voice.
   * `kept` names an offline-pack line to say instead of the text: what is
   * said while ATLAS is out of reach (the text on screen keeps the detail). */
  private answer(text: string, who = "ATLAS", kept?: string | null): void {
    this.replyN += 1;
    this.set({ reply: { text, who, n: this.replyN } });
    if (who !== "ATLAS") return;
    if (kept) { void this.speaker.sayKept(kept); return; }
    void this.speaker.say(text).then((r) => {
      if (!r.ok && r.problem !== "interrupted" && r.problem !== "ATLAS speaks is off") {
        this.caption(`Voice: ${r.problem}`, "AIR GLASS");
      }
    });
  }

  // ------------------------------------------------------- voice + gesture

  private context(): GestureContext {
    const s = this.history.latest;
    return { held: s?.held ?? null, shown: s?.shown ?? null, under: s?.under ?? null,
      meant: null, cursor: s?.cursor ?? null, place: null };
  }

  private entryNamed(n: Named | null): Entry | null {
    if (!n) return this.currentEntry;
    if (n.key === this.currentEntry?.key) return this.currentEntry;
    return null;
  }

  /** The page's own ears: one utterance, understood with the hand. */
  private hearUtterance(u: Utterance): void {
    const intent = interpret(u, this.history, this.names());
    this.set({ heard: `“${u.text.trim()}”${intent.kind === "ignore" ? ` (${intent.why})` : ""}` });
    void this.act(intent, "page");
  }

  /** The ATLAS Menu's ⌘ conversation. Everything said there is meant for
   * ATLAS; what is not about Air Glass is handed back to the Menu. */
  private hearMenu(text: string): boolean {
    const intent = interpret(utteranceEndingNow(text, nowS()), this.history, this.names());
    if (intent.kind === "general" || intent.kind === "ignore") return false;
    this.set({ heard: `“${text.trim()}” (from ATLAS Menu)` });
    void this.act(intent, "menu");
    return true;
  }

  private async act(intent: Intent, via: "page" | "menu"): Promise<void> {
    if (intent.kind === "ignore") return;
    if (intent.kind === "look") { await this.look(intent.question, intent.context); return; }
    if (intent.kind === "ask") { await this.askAbout(intent.question, intent.context); return; }
    if (intent.kind === "library") { await this.searchLibrary(intent.query, true); return; }
    if (intent.kind === "general") {
      if (via === "page") await this.askAtlas(intent.question);
      return;
    }
    const a = intent.action;
    switch (a.verb) {
      case "bigger": this.resize(1.25); break;
      case "smaller": this.resize(0.8); break;
      case "give": this.give(); break;
      case "reset": this.reset(); break;
      case "next": this.next(); break;
      case "stop": this.setSpinning(false); break;
      case "spin": this.setSpinning(true, a.speed); break;
      case "move": this.moveTo(a.to); break;
      case "pick": this.pick(a.key); break;
      case "explain": await this.explain(this.entryNamed(intent.thing)); break;
    }
  }

  /** "ATLAS, look": one still, now, to a vision model; kept nowhere. */
  async look(question: string, ctx: GestureContext = this.context()): Promise<void> {
    if (this.ui.looking) return;
    if (this.ui.source !== "camera" || !this.camera.live) {
      this.answer(this.ui.source === "demo"
        ? "I can only look through the camera, and the demo has none. Stop the demo first."
        : "I can only look while the camera is on.");
      return;
    }
    this.set({ looking: true });
    const shown = nowS();
    try {
      const still = await captureStill(this.els.video);
      if (!still) { this.answer("The camera has no picture yet, so I couldn't look."); return; }
      const about = this.entryNamed(ctx.meant ?? ctx.held ?? ctx.shown)?.about;
      const r = await fetch("/api/airglass/look", {
        method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin",
        body: JSON.stringify({ image: still.b64, question, context: { ...ctx, about } }),
      });
      const body = (await r.json().catch(() => ({}))) as { answer?: string; detail?: string };
      if (this.destroyed) return;
      this.answer(r.ok ? body.answer || "I looked, and have nothing to say about it."
        : body.detail || `I couldn't look just now (${r.status}).`);
    } catch (e) {
      this.answer(`I couldn't reach ATLAS to look (${String((e as Error)?.message ?? e)}).`,
        "ATLAS", unreachable(e));
    } finally {
      // The indicator stays up long enough to be seen, however fast the answer.
      const left = Math.max(0, 0.9 - (nowS() - shown));
      window.setTimeout(() => this.set({ looking: false }), left * 1000);
    }
  }

  /** A question about the hologram in hand, with what the hand is doing. */
  private async askAbout(question: string, ctx: GestureContext): Promise<void> {
    const about = this.entryNamed(ctx.meant ?? ctx.held ?? ctx.shown)?.about;
    try {
      const r = await fetch("/api/airglass/ask", {
        method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin",
        body: JSON.stringify({ question, context: { ...ctx, about } }),
      });
      const body = (await r.json().catch(() => ({}))) as { answer?: string; detail?: string };
      if (this.destroyed) return;
      this.answer(r.ok ? body.answer || "I have nothing to add." : body.detail || `I couldn't answer (${r.status}).`);
    } catch (e) {
      this.answer(`I couldn't reach ATLAS (${String((e as Error)?.message ?? e)}).`,
        "ATLAS", unreachable(e));
    }
  }

  /** Addressed to ATLAS and not about the thing: the ordinary conversation
   * (the same read-only /api/voice/ask the dashboard's microphone uses). */
  private async askAtlas(question: string): Promise<void> {
    try {
      const r = await fetch("/api/voice/ask", {
        method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin",
        body: JSON.stringify({ transcript: question }),
      });
      const body = (await r.json().catch(() => ({}))) as {
        answer?: string | null; spoken?: string | null; error?: string | null; detail?: string };
      if (this.destroyed) return;
      const text = r.ok ? body.spoken || body.answer || body.error || "No answer." : body.detail || `No answer (${r.status}).`;
      this.answer(text);
    } catch (e) {
      this.answer(`I couldn't reach ATLAS (${String((e as Error)?.message ?? e)}).`,
        "ATLAS", unreachable(e));
    }
  }

  // ------------------------------------------------------------- per frame

  private zoneRect(): { x: number; y: number; w: number; h: number } | null {
    if (!this.zoneEl) return null;
    const s = this.els.stage.getBoundingClientRect();
    const z = this.zoneEl.getBoundingClientRect();
    return { x: z.left - s.left, y: z.top - s.top, w: z.width, h: z.height };
  }

  private centreOf(sel: string): Pt | null {
    const el = this.els.root.querySelector<HTMLElement>(sel);
    if (!el) return null;
    const s = this.els.stage.getBoundingClientRect();
    const r = el.getBoundingClientRect();
    return { x: r.left - s.left + r.width / 2, y: r.top - s.top + r.height / 2 };
  }

  private readonly hit = (x: number, y: number): ReturnType<typeof hitBoxes> => hitBoxes(this.boxes, x, y);

  /** The model on show, as drawn: centre and radius in stage pixels. */
  private modelDisc(): ModelDisc | null {
    const st = this.stage;
    if (!st?.current) return null;
    const c = st.worldToScreen(st.holder.position);
    return { x: c.x, y: c.y, r: (SceneModule.MODEL_SIZE / 2) * st.holder.scale.x / st.unitsPerPx };
  }

  private cameraHands(): Pt[][] | null {
    const lm = this.camera.landmarker, v = this.els.video;
    if (!lm || !this.camera.live || v.readyState < 2 || v.currentTime === this.lastVideoTime) return null;
    this.lastVideoTime = v.currentTime;
    const res = lm.detectForVideo(v, performance.now());
    const vid = { w: v.videoWidth, h: v.videoHeight };
    const view = this.stage?.view ?? { w: this.els.stage.clientWidth, h: this.els.stage.clientHeight };
    return (res.landmarks ?? []).map((hand) => hand.map((p) => videoToScreen(p, vid, view, true)));
  }

  /** `dt`: seconds since the last frame. The demo runs on frame time, each
   * step capped, so a stall (the hand model starting up, a hidden tab) pauses
   * the script instead of skipping a step of it. */
  private demoHands(dt: number): Pt[][] {
    this.demoClock += Math.min(Math.max(dt, 0), 0.1);
    const t = this.demoClock;
    const loop = Math.floor(t / DEMO_LENGTH);
    if (loop !== this.demoLoop) {
      this.demoLoop = loop;
      // Each loop pulls out a model that is not already on show.
      if (this.ui.tab !== "model") this.set({ tab: "model" });
      const keys = this.tileKeys("model").filter((k) => k !== this.currentEntry?.key);
      this.demoTile = keys[loop % Math.max(1, keys.length)] ?? null;
    }
    const view = this.stage?.view ?? { w: this.els.stage.clientWidth, h: this.els.stage.clientHeight };
    const st = this.stage;
    const rest = st?.current && !this.holding ? st.worldToScreen(st.holder.position) : null;
    const f = demoFrame(t, view, {
      tile: this.demoTile ? this.centreOf(`[data-target='${this.demoTile}']`) : null,
      trayTop: this.trayY, zone: this.centreOf("[data-target='zone:atlas']"),
      model: this.demoRest ?? rest,
    });
    // Where the model was left, held fixed while the demo hand carries it.
    if (!this.holding) this.demoRest = rest;
    if (f.caption !== this.ui.demoCaption) this.set({ demoCaption: f.caption });
    return f.hands;
  }
  private demoRest: Pt | null = null;

  private readonly frame = (): void => {
    if (this.destroyed) return;
    this.raf = requestAnimationFrame(this.frame);
    const nowMs = performance.now();
    const dtMs = nowMs - this.lastFrame;
    this.lastFrame = nowMs;
    const dt = Math.min(dtMs / 1000, 0.1);
    this.fps = this.fps ? this.fps * 0.92 + (1000 / Math.max(dtMs, 1)) * 0.08 : 60;
    const now = nowMs / 1000;
    if (nowMs - this.boxesAt > 200) { this.boxesAt = nowMs; this.readBoxes(); }

    let raw: Pt[][] | null = null;
    try {
      raw = this.ui.source === "demo" ? this.demoHands(dtMs / 1000)
        : this.ui.source === "camera" ? this.cameraHands() : [];
    } catch (e) {
      console.warn("air glass frame:", e);
    }
    if (raw) {
      const tracked = this.tracker.update(raw, now);
      const out = this.engine.update(tracked, now, {
        hit: this.hit, model: this.modelDisc(), trayTop: this.trayY > 0 ? this.trayY : null,
      });
      this.lastOut = out;
      for (const e of out.events) this.handle(e, now);
      if (out.phase === "HOLD" && out.owner && this.holding) this.hold(out.owner.point, now);
      if (out.cursor) this.lowest.push({ t: now, y: out.cursor.y });
    }
    const out = this.lastOut;
    this.remember(out, now);
    this.paintHover(out);
    const pull = out?.pull ?? null;
    this.overlay.draw(this.tracker.hands, {
      cursorHand: out?.owner?.id ?? null, overZone: out?.overZone ?? false,
      hovering: Boolean(out?.hover),
      ghost: pull && this.layout ? { x: pull.x, y: pull.y, w: this.layout.tile.w * 0.8,
        h: this.layout.tile.h * 0.8, name: this.nameOf(pull.id) } : null,
    });
    this.paintHint(out);
    this.relayout(dt);

    const st = this.stage;
    if (!st) return;
    this.stepMotion(dt, now);
    st.render(!this.holding && !this.flight && !this.tween);
    this.placeReply();
    if (nowMs - this.lastKinetics > 150) {
      this.lastKinetics = nowMs;
      const p = st.holder.position;
      this.els.kinetics.textContent =
        `POS ${p.x.toFixed(2)} ${p.y.toFixed(2)} · SCALE ×${st.holder.scale.x.toFixed(2)}`;
      this.els.meter.textContent = `${this.ui.source.toUpperCase()} · HANDS ${this.tracker.hands.length}`
        + ` · ${(out?.phase ?? "IDLE")} · ${this.fps.toFixed(0)} FPS`;
    }
  };

  private handle(e: EngineEvent, now: number): void {
    const st = this.stage;
    if (e.type === "press") { this.onPress(e.id); return; }
    if (e.type === "pull-cancel") { this.caption("Slide it up out of the tray to bring it out.", "AIR GLASS"); return; }
    if (!st) return;
    if (e.type === "bring-out") {
      const at = { x: e.x, y: e.y };
      if (e.id === this.currentEntry?.key && st.current) {
        this.startHold(at, now, st.worldToScreen(st.holder.position));
      } else {
        const entry = this.entryFor(e.id);
        if (!entry) {
          // A library model not fetched yet: fetch it; it comes out in the middle.
          if (e.id.startsWith("lib:")) void this.loadLibrary(e.id.slice(4));
          return;
        }
        this.show(entry, at);
        this.startHold(at, now, at);
      }
      this.coach("picked");
    } else if (e.type === "grab") {
      if (!st.current) return;
      this.startHold({ x: e.x, y: e.y }, now, st.worldToScreen(st.holder.position));
      this.coach("grabbed");
    } else if (e.type === "release") {
      if (!this.holding) return;
      this.holding = false;
      if (e.drop) { this.caught(); return; }
      const m = releaseMotion({ vx: e.vx, vy: e.vy, speed: Math.hypot(e.vx, e.vy) }, st.view.w, this.reduced);
      const k = st.unitsPerPx;
      this.flight = m.glide.vx || m.glide.vy ? {
        vx: m.glide.vx * k, vy: -m.glide.vy * k, spin: 0, damping: TUNING.glideDamping, toZone: false,
      } : null;
      st.spin = m.spin;
      this.coach("grabbed");
    }

  }

  private startHold(point: Pt, now: number, modelAt: Pt): void {
    const st = this.stage;
    if (!st) return;
    this.flight = null;
    this.tween = null;
    st.spin = { x: 0, y: 0 };
    this.follow.start(point, modelAt, now);
    this.holding = true;
  }

  private hold(point: Pt, now: number): void {
    const st = this.stage;
    if (!st?.current) return;
    const o = this.follow.update(point, now, st.view.w, this.reduced);
    const w = st.screenToWorld(o.x, o.y);
    st.holder.position.set(w.x, w.y, st.holder.position.z);
    st.roll(o.ax, o.ay);
  }

  private onPress(id: string): void {
    if (id.startsWith("tab:")) { this.setTab(id.slice(4) as Source); return; }
    if (id === "btn:bigger") this.resize(1.25);
    else if (id === "btn:smaller") this.resize(0.8);
    else if (id === "btn:reset") this.reset();
    else if (id === "btn:explain") void this.explain();
    else if (id === "btn:give") this.give();
    else if (id === "btn:look") void this.look("What am I holding?");
    else if (id === "btn:mic") void this.toggleListen();
    else if (id === "btn:add") this.caption("Adding a model needs the mouse: click + Add model, or drop a .glb here.", "AIR GLASS");
  }

  /** One snapshot of what the hand is doing, for "this" and "here". */
  private remember(out: EngineOut | null, t: number): void {
    const cur = this.currentEntry && this.stage?.current
      ? { key: this.currentEntry.key, name: this.currentEntry.name } : null;
    let under: Snap["under"] = null;
    const hv = out?.hover ?? null;
    if (hv?.kind === "model" && cur) under = { ...cur, kind: "model" };
    else if (hv && hv.kind !== "model") under = { key: hv.id, name: this.nameOf(hv.id), kind: hv.kind === "tile" ? "tile" : "button" };
    this.history.push({ t, cursor: out?.cursor ? { x: out.cursor.x, y: out.cursor.y } : null,
      held: this.holding ? cur : null, shown: cur, under });
  }

  private stepMotion(dt: number, now: number): void {
    const st = this.stage;
    if (!st?.current) return;
    const h = st.holder;
    if (this.spawn) {
      const k = smoothstep((now - this.spawn.t0) / 0.15);
      h.scale.setScalar(Math.max(0.001, this.spawn.scale * k));
      if (k >= 1) this.spawn = null;
    }
    if (this.tween) {
      const tw = this.tween;
      const k = tw.dur > 0 ? smoothstep((now - tw.t0) / tw.dur) : 1;
      const w = st.screenToWorld(tw.from.x + (tw.to.x - tw.from.x) * k, tw.from.y + (tw.to.y - tw.from.y) * k);
      h.position.set(w.x, w.y, h.position.z);
      if (k >= 1) this.tween = null;
      return;
    }
    const f = this.flight;
    if (!f || this.holding) return;
    const b = stepFlight({ x: h.position.x, y: h.position.y, vx: f.vx, vy: f.vy }, dt, f.damping);
    h.position.x = b.x; h.position.y = b.y;
    f.vx = b.vx; f.vy = b.vy;
    if (f.spin) { st.roll(0, f.spin * dt); f.spin *= Math.exp(-1.6 * dt); }
    if (f.toZone) {
      const s = st.worldToScreen(h.position);
      const z = this.zoneRect();
      if (z && s.x >= z.x && s.y >= z.y - 40 && s.y <= z.y + z.h + 40) { this.caught(); return; }
    }
    // Nothing glides off the stage: it stops softly at the edges.
    const halfW = st.view.w / 2 * st.unitsPerPx, halfH = st.view.h / 2 * st.unitsPerPx;
    if (Math.abs(h.position.x) > halfW * 0.9) { h.position.x = Math.sign(h.position.x) * halfW * 0.9; f.vx = 0; }
    if (Math.abs(h.position.y) > halfH * 0.85) { h.position.y = Math.sign(h.position.y) * halfH * 0.85; f.vy = 0; }
    if (Math.hypot(f.vx, f.vy) < 0.02 && !f.toZone) this.flight = null;
  }

  /** The answer sits beside the object it is about. */
  private placeReply(): void {
    const el = this.replyEl, st = this.stage;
    if (!el || !st) return;
    const d = this.modelDisc();
    const view = st.view;
    const w = el.offsetWidth || 300, hgt = el.offsetHeight || 60;
    let x = view.w * 0.5 - w / 2, y = view.h * 0.26;
    if (d) {
      x = d.x + d.r + 16;
      if (x + w > view.w - 190) x = d.x - d.r - 16 - w;
      y = d.y - hgt / 2;
    }
    el.style.transform = `translate(${Math.round(clamp(x, 12, view.w - w - 12))}px, ${Math.round(clamp(y, 60, view.h - hgt - 12))}px)`;
  }

  private paintHover(out: EngineOut | null): void {
    const hv = out?.hover;
    const id = hv && hv.kind !== "model" ? hv.id : null;
    const el = id ? this.els.root.querySelector<HTMLElement>(`[data-target='${id}']`) : null;
    if (el !== this.hovered) {
      this.hovered?.removeAttribute("data-hover");
      el?.setAttribute("data-hover", "true");
      this.hovered = el;
    }
    const pulling = out?.pull?.id ?? null;
    for (const t of this.els.root.querySelectorAll<HTMLElement>(".ag-tile[data-pulling='true']")) {
      if (t.dataset.target !== pulling) t.removeAttribute("data-pulling");
    }
    if (pulling) this.els.root.querySelector<HTMLElement>(`[data-target='${pulling}']`)?.setAttribute("data-pulling", "true");
    const z = this.zoneEl;
    if (z) {
      z.dataset.armed = String(this.holding);
      z.dataset.over = String(Boolean(out?.overZone));
    }
  }

  private paintHint(out: EngineOut | null): void {
    const hv = out?.hover ?? null;
    const hover = hv ? { kind: hv.kind, name: hv.kind === "model" ? "model" : this.nameOf(hv.id) } : null;
    const text = this.ui.message && this.ui.source === "none" ? hintFor({
      source: "none", hands: 0, phase: "IDLE", hasModel: false, overZone: false, hover: null,
    }) : hintFor({
      source: this.ui.source === "none" ? "camera" : this.ui.source,
      hands: this.tracker.hands.length, phase: out?.phase ?? "IDLE",
      hasModel: Boolean(this.stage?.current), overZone: out?.overZone ?? false, hover,
    });
    if (text !== this.lastHint) {
      this.lastHint = text;
      this.els.hint.textContent = text;
    }
  }

  // --------------------------------------------------------------- teardown

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    cancelAnimationFrame(this.raf);
    this.offs.forEach((off) => off());
    this.resizeObs?.disconnect();
    if (this.healthTimer !== null) window.clearInterval(this.healthTimer);
    this.healthTimer = null;
    this.listener.stop("off");
    this.speaker.destroy();
    this.currentEntry?.stop?.();
    this.camera.close();
    this.stage?.dispose();
    this.stage = null;
    const w = window as unknown as { __airglass?: unknown; __airglassCommand?: unknown };
    delete w.__airglass;
    delete w.__airglassCommand;
  }
}
