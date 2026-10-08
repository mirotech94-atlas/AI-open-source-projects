import { useEffect, useRef, useState, type JSX } from "react";
import { useLocation } from "react-router-dom";
import { AirGlass as Controller, type UiState } from "../airglass/app";
import { PRODUCTS, type Source } from "../airglass/catalog";
import { COACH_STEPS, coachCurrent } from "../airglass/coach";
import { PANELS } from "../airglass/data";
import { LEGEND } from "../airglass/gestures";
import { formatSize } from "../airglass/library";
import { PROPS } from "../airglass/scene";
import "../airglass/airglass.css";

/**
 * Air Glass: a hand-tracking HUD over the webcam. One gesture, either hand:
 * pinch a model in the tray and slide it up to bring it out; pinch it and
 * slide to move and turn it; let go over ATLAS to give it. Talk to it while
 * you do ("make this bigger", "put it here", "ATLAS, look"): listening is on
 * while the page is open, and the Mic button mutes it.
 *
 * Frames stay in this tab, except ONE still, taken only when he asks ATLAS
 * to look, sent to /api/airglass/look and kept nowhere.
 *
 * While this page is open it has the camera to itself: `airglass/camera.ts`
 * borrows it from the shared hand controller (`useHand.ts::lendCamera`), so
 * pinch-to-wake and the gesture cursor pause here and resume on the way out.
 *
 * `bare` is the ATLAS Menu's Air Glass window (/airglass?window=1): the
 * page alone, without the command center around it.
 */
const SHELVES: ReadonlyArray<{ tab: Source; name: string }> = [
  { tab: "model", name: "Models" },
  { tab: "data", name: "Your ATLAS data" },
  { tab: "academy", name: "ATLAS Academy" },
  { tab: "library", name: "Free library" },
];

const MIC_LABEL: Record<UiState["listen"], string> = {
  off: "Mic off", starting: "Mic …", "on-device": "Listening · on this Mac",
  server: "Listening", muted: "Muted", unavailable: "Mic unavailable",
};

export function AirGlass({ bare = false, pinchWake = false }: {
  bare?: boolean;
  pinchWake?: boolean;
}): JSX.Element {
  const location = useLocation();
  const root = useRef<HTMLDivElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const gl = useRef<HTMLCanvasElement>(null);
  const hands = useRef<HTMLCanvasElement>(null);
  const hint = useRef<HTMLParagraphElement>(null);
  const kinetics = useRef<HTMLSpanElement>(null);
  const meter = useRef<HTMLSpanElement>(null);
  const file = useRef<HTMLInputElement>(null);
  const ctl = useRef<Controller | null>(null);
  const [ui, setUi] = useState<UiState | null>(null);
  const [captionShown, setCaptionShown] = useState(false);
  const [replyShown, setReplyShown] = useState(false);
  const [libQuery, setLibQuery] = useState("");
  const demo = new URLSearchParams(location.search).has("demo");

  useEffect(() => {
    const els = {
      root: root.current, stage: stage.current, video: video.current, gl: gl.current,
      hands: hands.current, hint: hint.current, kinetics: kinetics.current, meter: meter.current,
    };
    if (!els.root || !els.stage || !els.video || !els.gl || !els.hands || !els.hint
        || !els.kinetics || !els.meter) return;
    const c = new Controller(els as Required<{ [K in keyof typeof els]: NonNullable<(typeof els)[K]> }>,
      setUi, { demo });
    ctl.current = c;
    setUi(c.ui);
    return (): void => {
      c.destroy();
      if (ctl.current === c) ctl.current = null;
    };
    // The controller owns the page for as long as it is mounted.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Captions and answers show for a few seconds each; longer ones longer.
  const caption = ui?.caption ?? null;
  useEffect(() => {
    if (!caption) return;
    setCaptionShown(true);
    const t = window.setTimeout(() => setCaptionShown(false),
      Math.min(12000, 2600 + caption.text.length * 45));
    return (): void => window.clearTimeout(t);
  }, [caption]);
  const reply = ui?.reply ?? null;
  useEffect(() => {
    if (!reply) return;
    setReplyShown(true);
    const t = window.setTimeout(() => setReplyShown(false),
      Math.min(16000, 4000 + reply.text.length * 55));
    return (): void => window.clearTimeout(t);
  }, [reply]);

  const c = ctl.current;
  const step = ui ? coachCurrent(ui.coach) : null;
  const tab = ui?.tab ?? "model";
  const lib = ui?.library ?? null;
  type Tile = { key: string; name: string; glyph: string; img: string | null; thumb?: boolean;
    sub?: string; title?: string };
  const tiles: Tile[] = tab === "model" ? [
    ...PROPS.map((p) => ({ key: `tile:${p.id}`, name: p.name, glyph: p.glyph, img: null })),
    ...(ui?.custom ?? []).map((m) => ({ key: `tile:${m.id}`, name: m.name, glyph: "▣", img: null })),
  ] : tab === "data" ? PANELS.map((p) => ({ key: `data:${p.kind}`, name: p.name, glyph: p.glyph, img: null }))
    : tab === "academy" ? PRODUCTS.map((p) => ({ key: `shop:${p.slug}`, name: p.title, glyph: "▤", img: p.cover }))
      : (lib?.models ?? []).map((m) => ({
        key: `lib:${m.id}`, name: m.name, glyph: "◇", img: m.thumb, thumb: true,
        sub: lib?.loading === m.id ? "fetching…" : `${m.licenseName} · ${formatSize(m.sizeBytes)}`,
        title: `${m.name} · ${m.attribution}`,
      }));
  const credit = ui?.credit ?? null;
  const health = ui?.health ?? { state: "OFF", detail: "", at: null };
  const clock = (d: Date): string => d.toLocaleTimeString([], { hour12: false });
  const listen = ui?.listen ?? "off";
  const live = listen === "on-device" || listen === "server";
  const speech = listen === "on-device" ? " Speech is recognised on this Mac."
    : listen === "server" ? " When you speak, those words go to ATLAS's transcriber." : "";

  return (
    <div className={`ag-root${bare ? " ag-bare" : ""}`} ref={root}
         data-source={ui?.source ?? "none"} data-dragging={ui?.dragging ? "true" : "false"}>
      <div className="ag-stage" ref={stage}>
        <video ref={video} className="ag-cam" data-live={ui?.cameraLive ? "true" : "false"}
               playsInline muted autoPlay />
        <div className="ag-scrim" aria-hidden="true" />
        <canvas ref={gl} className="ag-gl" />
        <canvas ref={hands} className="ag-hands" />

        <header className="ag-top">
          <span className="ag-brand"><b>AIR GLASS</b><i>ATLAS · LOCAL</i></span>
          <span className="ag-cam-ind" data-on={ui?.cameraLive ? "true" : "false"}
                title="The camera is open only while this page is">
            <span className="d" aria-hidden="true" />
            {ui?.cameraLive ? "Camera on" : ui?.source === "demo" ? "Demo hand" : "Camera off"}
          </span>
          <button type="button" className="ag-mic" data-state={listen}
                  aria-pressed={live} onClick={() => void c?.toggleListen()}
                  title={ui?.listenDetail || "Listening while this page is open"}>
            <span className="d" aria-hidden="true" />
            {ui?.speaking ? "ATLAS speaking" : MIC_LABEL[listen]}
            <small>{live ? "tap to mute" : listen === "muted" ? "tap to listen" : ""}</small>
          </button>
          {ui?.looking && (
            <span className="ag-looking" role="status">ATLAS is looking…</span>
          )}
          <span className="ag-meter" ref={meter}>NONE · HANDS 0 · IDLE</span>
          <span className="ag-meter ag-kin" ref={kinetics} />
          {health.state !== "OFF" && (
            <span className="ag-orb" data-state={health.state}
                  title={health.at ? `${health.detail} · read ${clock(health.at)}` : health.detail}>
              <span className="d" aria-hidden="true" />
              <b>{health.state}</b>
              <small>{health.at ? `read ${clock(health.at)}` : health.detail}</small>
            </span>
          )}
          <span className="ag-tools">
            <button type="button" onClick={() => c?.toggleDemo()}
                    aria-pressed={ui?.source === "demo"}>Demo</button>
            <button type="button" onClick={() => c?.toggleLook()}
                    aria-pressed={ui?.hologram ?? true}>{ui?.hologram === false ? "Solid" : "Hologram"}</button>
            <button type="button" onClick={() => c?.toggleVoice()} aria-pressed={ui?.voiceOn ?? true}
                    title="ATLAS's own voice (Fish Audio)">ATLAS speaks {ui?.voiceOn === false ? "off" : "on"}</button>
            <button type="button" onClick={() => c?.toggleStatus()} aria-pressed={health.state !== "OFF"}>
              Status
            </button>
            <button type="button" onClick={() => file.current?.click()}>+ Add model</button>
            {!bare && (
              <button type="button" onClick={() => {
                if (document.fullscreenElement) void document.exitFullscreen();
                else void root.current?.requestFullscreen?.();
              }}>Full screen</button>
            )}
            <button type="button" className="ag-reset" data-target="btn:reset" data-kind="button"
                    data-name="Reset" onClick={() => c?.reset()}>Reset</button>
          </span>
        </header>

        <p className="ag-privacy">
          Frames stay on this Mac except one still, when you ask ATLAS to look.{speech}
          {pinchWake && " Pinch-to-wake is paused while Air Glass has the camera."}
        </p>

        <div className="ag-side" role="group" aria-label="Actions">
          <div className="ag-size">
            <button type="button" className="ag-round" data-target="btn:bigger" data-kind="button"
                    data-name="Bigger" onClick={() => c?.resize(1.25)} aria-label="Bigger">+</button>
            <button type="button" className="ag-round" data-target="btn:smaller" data-kind="button"
                    data-name="Smaller" onClick={() => c?.resize(0.8)} aria-label="Smaller">−</button>
          </div>
          <button type="button" className="ag-pill ag-ask" data-target="btn:look" data-kind="button"
                  data-name="Ask ATLAS" onClick={() => void c?.look("What am I holding?")}
                  title="ATLAS looks through the camera: one still, now, kept nowhere">Ask ATLAS</button>
          <button type="button" className="ag-pill" data-target="btn:explain" data-kind="button"
                  data-name="Explain" onClick={() => void c?.explain()}>Explain</button>
          <button type="button" className="ag-pill" data-target="btn:give" data-kind="button"
                  data-name="Give to ATLAS" onClick={() => c?.give()}>Give to ATLAS</button>
        </div>

        <div className="ag-zone" data-target="zone:atlas" data-kind="zone" data-name="ATLAS"
             role="button" tabIndex={0} title="Give it to ATLAS"
             onClick={() => c?.give()} onKeyDown={(e) => { if (e.key === "Enter") c?.give(); }}>
          <span className="ring" aria-hidden="true" />
          <b>ATLAS</b>
          <small className="when-idle">let go here to give it</small>
          <small className="when-armed">let go to give it</small>
          {(ui?.vault.length ?? 0) > 0 && (
            <ul className="ag-vault" aria-label="Given to ATLAS">
              {ui?.vault.slice(0, 4).map((v, i) => (
                <li key={`${v.name}-${v.at.getTime()}-${i}`}>{v.name}<time>{clock(v.at)}</time></li>
              ))}
            </ul>
          )}
        </div>

        <div className="ag-trayband">
          <p className="ag-hint" ref={hint} aria-live="polite">{LEGEND}</p>
          {tab === "library" && lib && (
            <form className="ag-libbar" role="search" aria-label="Free 3D library"
                  onSubmit={(e) => { e.preventDefault(); void c?.searchLibrary(libQuery); }}>
              <input type="search" value={libQuery} maxLength={80} placeholder="car, helmet, chair…"
                     aria-label="Search the free 3D library"
                     onChange={(e) => setLibQuery(e.target.value)} />
              <button type="submit" disabled={lib.state === "searching"}>Search</button>
              <small>
                Poly Haven (CC0) · Khronos glTF Sample Assets (CC0 / CC BY), through ATLAS
                {lib.readAt && ` · read ${clock(lib.readAt)}`}
                {lib.down && <em> · {lib.down}</em>}
              </small>
            </form>
          )}
          <div className="ag-shelves" role="tablist" aria-label="Shelves">
            {SHELVES.map((s) => (
              <button type="button" key={s.tab} role="tab" aria-selected={tab === s.tab}
                      data-target={`tab:${s.tab}`} data-kind="button" data-name={s.name}
                      onClick={() => c?.setTab(s.tab)}>{s.name}</button>
            ))}
          </div>
          <div className="ag-tray" role="list" aria-label="Holograms">
            {tiles.map((t, i) => {
              const active = ui?.current === t.key;
              return (
                <button type="button" key={t.key} role="listitem" className="ag-tile"
                        data-target={t.key} data-kind="tile" data-name={t.name}
                        data-active={active ? "true" : "false"} title={t.title}
                        onClick={() => c?.pick(t.key)}>
                  {i < 9 && <span className="k" aria-hidden="true">{i + 1}</span>}
                  {t.img
                    ? <img className={t.thumb ? "thumb" : "cover"} src={t.img} alt="" loading="lazy" />
                    : <span className="g" aria-hidden="true">{t.glyph}</span>}
                  <span className="n">{t.name}</span>
                  {t.sub && <small>{t.sub}</small>}
                </button>
              );
            })}
            {tab === "model" && (
              <button type="button" className="ag-tile ag-add" data-target="btn:add" data-kind="button"
                      data-name="Add model" onClick={() => file.current?.click()}>
                <span className="g" aria-hidden="true">＋</span>
                <span className="n">Add model</span>
                <small>.glb / .gltf</small>
              </button>
            )}
            {tab === "library" && lib && lib.models.length === 0 && (
              <div className="ag-soon" role="status">
                {lib.state === "searching" && <b>Searching the free library…</b>}
                {lib.state === "error" && <><b>The free library could not be searched</b><span>{lib.detail}</span></>}
                {lib.state === "ok" && (
                  <><b>Nothing in the free library for “{lib.query}”</b>
                    {lib.down && <span>{lib.down}</span>}</>
                )}
                {lib.state === "idle" && <b>Search the free library, or say “show me a car”.</b>}
              </div>
            )}
            <input ref={file} type="file" hidden multiple
                   accept=".glb,.gltf,.bin,.png,.jpg,.jpeg,.webp"
                   onChange={(e) => {
                     if (e.target.files?.length) void c?.openFiles(Array.from(e.target.files));
                     e.target.value = "";
                   }} />
          </div>
        </div>

        <div className="ag-legend" aria-label="How to use it">
          <span>{LEGEND}</span>
          {ui?.heard && <small className="ag-heard">heard {ui.heard}</small>}
          <span className="ag-switches">
            <button type="button" aria-pressed={ui?.handsOn ?? true} onClick={() => c?.toggleHands()}>
              Hands {ui?.handsOn === false ? "off" : "on"}
            </button>
            <button type="button" aria-pressed={ui?.keysOn ?? true} onClick={() => c?.toggleKeys()}
                    title="1–9 bring out · ←→↑↓ turn · + − size · R reset · A give · E explain · L look · M mute">
              Keys {ui?.keysOn === false ? "off" : "on"}
            </button>
          </span>
        </div>

        {credit && (
          <p className="ag-credit" data-required={credit.required ? "true" : "false"}>
            <b>{credit.name}</b> · {credit.text}
            {credit.required && <> · <span>{credit.license}, credit required</span></>}
          </p>
        )}

        {ui?.demoCaption && ui.source === "demo" && <div className="ag-demo-cap">{ui.demoCaption}</div>}

        <div className={`ag-reply${replyShown ? " show" : ""}`} aria-live="polite">
          {reply && <><span className="who">{reply.who}</span>{reply.text}</>}
        </div>

        <div className={`ag-caption${captionShown ? " show" : ""}`} aria-live="polite">
          {caption && <><span className="who">{caption.who}</span>{caption.text}</>}
        </div>

        {ui?.looking && <div className="ag-flash" aria-hidden="true" />}

        {step !== null && ui && (
          <aside className="ag-coach" aria-label="How Air Glass works">
            <ol>
              {COACH_STEPS.map((s, i) => (
                <li key={s.event} data-state={ui.coach.done.includes(s.event) ? "done"
                  : i === step ? "now" : "next"}>
                  <span className="k">{i + 1}</span>
                  <span><b>{s.title}</b><small>{s.detail}</small></span>
                </li>
              ))}
            </ol>
            <button type="button" onClick={() => c?.dismissCoach()}>Got it</button>
          </aside>
        )}

        {ui?.dragging && (
          <div className="ag-drop"><p>Drop a .glb or .gltf<br /><small>read here, never uploaded</small></p></div>
        )}

        {ui?.message && ui.source !== "demo" && (
          <div className="ag-message" role="alertdialog" aria-labelledby="ag-msg-title">
            <div className="box">
              <h1 id="ag-msg-title">{ui.message.title}</h1>
              <p>{ui.message.body}</p>
              <div className="actions">
                {ui.message.kind !== "webgl" && (
                  <button type="button" onClick={() => c?.toggleDemo()}>Start the demo</button>
                )}
                {ui.message.kind === "camera" && (
                  <button type="button" className="ghost" onClick={() => void c?.retryCamera()}>
                    Try the camera again
                  </button>
                )}
                {ui.message.kind !== "webgl" && (
                  <button type="button" className="ghost" onClick={() => c?.dismissMessage()}>
                    Use buttons and keys
                  </button>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
