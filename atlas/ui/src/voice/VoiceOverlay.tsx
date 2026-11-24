import { useEffect, useRef, type JSX } from "react";
import { api, normaliseHealth, type SystemCheck } from "../api";
import { HealthBadge } from "../components/Health";
import { useFresh } from "../hooks/useFresh";
import { coreLabel, micBadgeAction, voiceHint } from "./detectorCore";
import { graphLayout, planFocus } from "./focus";
import type { Turn, useVoice, VoiceSource } from "./useVoice";
import type { Phase } from "./loop";
import { tapLabel } from "./interrupt";
import { SpeakingSparks } from "./SpeakingSparks";

const BARS = 38;

/** A short rising tone on wake. Synthesised, so there is no asset to fetch. */
/** A reading as a person would say it: 494.67699999999996 is 495. */
function figure(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return String(value);
  return value.toLocaleString(undefined, { maximumFractionDigits: Math.abs(value) >= 100 ? 0 : 1 });
}

function chime(): void {
  try {
    const context = new AudioContext();
    const osc = context.createOscillator();
    const gain = context.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(440, context.currentTime);
    osc.frequency.exponentialRampToValueAtTime(760, context.currentTime + 0.09);
    gain.gain.setValueAtTime(0.0001, context.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.06, context.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + 0.1);
    osc.connect(gain).connect(context.destination);
    osc.start();
    osc.stop(context.currentTime + 0.11);
    window.setTimeout(() => void context.close(), 300);
  } catch {
    /* autoplay policy, or no audio device. Not worth surfacing. */
  }
}

/**
 * The persistent mic indicator.
 *
 * It reports what the device is actually doing, never what the app intended,
 * and clicking it cuts the microphone at the browser level — the tracks are
 * stopped, not muted, so the OS indicator goes dark too.
 */
export function MicBadge({ voice }: { voice: ReturnType<typeof useVoice> }): JSX.Element {
  const phrase = voice.detector === "porcupine" || voice.detector === "local-whisper"
    || voice.detector === "speech-api";
  const label: Record<string, string> = {
    unavailable: "No microphone",
    denied: "Mic blocked",
    off: voice.handsFree ? "Mic off" : "Muted",
    // Only a phrase detector is listening for the word. An open mic with
    // pinch or click-to-talk behind it is a conversation, not a wake gate.
    listening: phrase && voice.phase === "dormant" ? "Listening for “atlas”" : "Mic open",
    streaming: "Streaming to ATLAS",
  };
  // The badge mutes, unmutes or restarts; it never wakes (see `micBadgeAction`).
  const action = micBadgeAction(voice);
  return (
    <button
      className="micbadge"
      data-mic={voice.mic}
      data-muted={String(!voice.handsFree)}
      onClick={voice.toggleMic}
      title={
        voice.mic === "denied"
          ? "Permission was refused. Allow the microphone in your browser settings."
          : voice.mic === "unavailable"
            ? "No microphone is available on this device."
            : action === "restart"
              ? "Mic is off — tap to start listening again"
            : action === "unmute"
              ? "Muted — tap to turn hands-free listening (and pinch-to-wake) back on"
              : action === "mute"
                ? voice.detector === "pinch"
                  ? "Tap to mute — stops pinch-to-wake (and releases the mic, if open) "
                    + "until you tap again. To talk: pinch, or tap the core."
                  : "Tap to mute — releases the microphone and stops listening for "
                    + "“atlas” until you tap again"
                : voice.detectorProblem
                  ? voice.detectorProblem
                  : "No wake detector armed — tap the core or “Talk to ATLAS” to speak"
      }
    >
      <span className="micdot" aria-hidden="true" />
      {voice.detector === "pinch" && voice.mic === "off" && voice.handsFree
        ? "Mic off · pinch on"
        : label[voice.mic] ?? "Mic"}
    </button>
  );
}

/** Mute / unmute beside the pill -- the badge's own action, so it is there
 *  whenever the badge would mute: an open mic, or a dormant pinch. */
export function MuteButton({ voice }: { voice: ReturnType<typeof useVoice> }): JSX.Element | null {
  const action = micBadgeAction(voice);
  if (action === "restart") return null;
  return (
    <button className="voice-mute" data-action={action} onClick={voice.toggleMic}
            title={action === "mute"
              ? "Releases the microphone and stops the wake detector — pinch-to-wake "
                + "included — until you tap again"
              : "Turns hands-free listening back on"}>
      {action === "mute" ? "Tap to mute" : "Tap to unmute"}
    </button>
  );
}

/** "Tap to stop" beside the pill while ATLAS thinks or speaks: ATLAS goes
 *  quiet and his next turn starts, in the same conversation. Unlike mute,
 *  nothing is released and the conversation stays open. */
export function StopButton({ voice }: { voice: ReturnType<typeof useVoice> }): JSX.Element | null {
  const label = tapLabel(voice.phase);
  if (!label) return null;
  return (
    <button className="voice-mute voice-stop" data-action="stop" onClick={voice.interrupt}
            title="Stops ATLAS talking and listens to you now">
      {label}
    </button>
  );
}

/**
 * The lines under the core: which detector is live, in words (or muted, or
 * the detector's problem), and what is not configured. The overlay draws
 * them under its core; the AI Core page, where the overlay draws nothing,
 * draws them under its own HUD -- the same component, so the two cannot say
 * different things.
 */
export function VoiceHints({ voice, always = false }: {
  voice: ReturnType<typeof useVoice>;
  /** Show the "Not configured" line while dormant too (the stage does). */
  always?: boolean;
}): JSX.Element | null {
  const hint = voiceHint(voice);
  const missing = voice.capabilities?.missing ?? [];
  const showMissing = missing.length > 0 && (always || voice.phase !== "dormant");
  const source = voice.capabilities?.voice_source;
  const showSource = !!source && (always || voice.phase !== "dormant");
  if (!hint && !showMissing && !showSource) return null;
  return (
    <div className="voice-hints">
      {hint && <p className="stamp voice-hint">{hint}</p>}
      {showSource && source && <VoiceSourceLine source={source} />}
      {showMissing && (
        <p className="missing">Not configured: {missing.join(" · ")}</p>
      )}
    </div>
  );
}

/** Which voice speaks, its health with the time it was read, and that it is
 *  an AI voice. UNKNOWN is grey and dashed with the word shown, never OK. */
function VoiceSourceLine({ source }: { source: VoiceSource }): JSX.Element {
  const read = new Date(source.as_of);
  const at = Number.isNaN(read.getTime()) ? "?"
    : read.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const ok = source.health === "ok";
  const fallback = source.speaking === "fish" ? "Fish Audio speaks"
    : source.speaking === "none" ? "no voice" : "";
  return (
    <p className="voice-source" data-health={ok ? "ok" : "unknown"}>
      <i aria-hidden="true" />
      {ok ? `Voice: ${source.voice}`
        : `Voice: ${source.voice} UNKNOWN${source.reason ? ` (${source.reason})` : ""}`
          + (fallback ? ` · ${fallback}` : "")}
      {` · ${source.disclosure} · read ${at}`}
    </p>
  );
}

/**
 * The turns of this conversation, oldest first, each with the time it was
 * answered. The answer being typed right now is not here yet -- it joins
 * once it has been given -- so no exchange is shown twice.
 */
export function TranscriptLog({ turns }: { turns: Turn[] }): JSX.Element | null {
  if (turns.length === 0) return null;
  return (
    <ol className="voice-log" aria-label="This conversation">
      {turns.map((turn, i) => (
        <li key={`${turn.at.getTime()}-${i}`}>
          <span className="t">
            {turn.at.toLocaleTimeString([], { hour12: false })}
          </span>
          <span className="q">“{turn.said}”</span>
          <span className={turn.answer ? "a" : "a problem"}>
            {turn.answer ?? turn.problem ?? ""}
          </span>
        </li>
      ))}
    </ol>
  );
}

function clock(iso: string | null | undefined): string {
  if (!iso) return "never read";
  const at = new Date(iso);
  return Number.isNaN(at.getTime())
    ? "never read" : at.toLocaleTimeString([], { hour12: false, hour: "2-digit",
                                                 minute: "2-digit" });
}

const GRAPH = 180;

/** Concierge in the middle, every agent around it, each edge lit when that
 *  agent can run and dashed grey when it cannot. Nodes appear on the same
 *  60 ms stagger as the checklist rows beside them. */
function AgentGraph({ agents }: { agents: SystemCheck["agents"] }): JSX.Element {
  const { centre, nodes } = graphLayout(agents.map((a) => a.name), GRAPH);
  const online = new Map(agents.map((a) => [a.name, a.online]));
  const mid = GRAPH / 2;
  return (
    <svg className="sc-graph" viewBox={`0 0 ${GRAPH} ${GRAPH}`} role="img"
         aria-label={`${centre ?? "ATLAS"} and ${nodes.length} agents`}>
      {nodes.map((n, i) => (
        <line key={`e-${n.name}`} x1={mid} y1={mid} x2={n.x} y2={n.y}
              className="sc-edge" data-online={String(online.get(n.name) === true)}
              style={{ ["--i" as string]: String(i + 1) }} />
      ))}
      {nodes.map((n, i) => (
        <circle key={`n-${n.name}`} cx={n.x} cy={n.y} r={5} className="sc-node"
                data-online={String(online.get(n.name) === true)}
                style={{ ["--i" as string]: String(i + 1) }}>
          <title>{`${n.name}: ${online.get(n.name) ? "online" : "offline"}`}</title>
        </circle>
      ))}
      <circle cx={mid} cy={mid} r={9} className="sc-node sc-centre"
              data-online={String(centre ? online.get(centre) === true : true)}
              style={{ ["--i" as string]: "0" }} />
      <text x={mid} y={mid + 22} textAnchor="middle" className="sc-label">
        {centre ?? "ATLAS"}
      </text>
    </svg>
  );
}

/**
 * The system check, drawn from the same read the line was spoken from:
 * agent rows flipping to ONLINE / OFFLINE on a 60 ms stagger, then one row
 * per module with its health word -- UNKNOWN in the grey, dashed, worded
 * treatment it has everywhere else, never sharing OK's.
 */
export function SystemCheckPanel({ check }: { check: SystemCheck }): JSX.Element {
  const agents = check.agents.length;
  return (
    <section className="syscheck" aria-label="System check">
      <AgentGraph agents={check.agents} />
      <ol className="sc-list">
        {check.agents.map((a, i) => (
          <li key={`a-${a.name}`} className="sc-row" data-online={String(a.online)}
              style={{ ["--i" as string]: String(i) }}>
            <span className="n">{a.title}</span>
            <span className="c">{a.online ? "" : a.reason ?? ""}</span>
            <span className="st">{a.online ? "ONLINE" : "OFFLINE"}</span>
          </li>
        ))}
        {check.modules.map((m, j) => (
          <li key={`m-${m.name}`} className="sc-row sc-module"
              data-health={normaliseHealth(m.health)}
              style={{ ["--i" as string]: String(agents + j) }}
              title={m.reason ?? undefined}>
            <span className="n">{m.title}</span>
            <span className="c">
              {m.not_measured
                ? `${m.enabled ? `${m.checks_ok}/${m.checks_total} · ` : "not loaded · "}${m.not_measured}`
                : `${m.checks_ok}/${m.checks_total}`}
            </span>
            <span className="st"><HealthBadge health={m.health} /></span>
          </li>
        ))}
      </ol>
      <p className="stamp sc-stamp">
        read {clock(check.as_of)}
        {check.oldest_reading ? ` · oldest reading ${clock(check.oldest_reading)}` : ""}
        {check.unreadable ? ` · ${check.unreadable}` : ""}
      </p>
    </section>
  );
}

/**
 * The tile of the module a spoken status named, beside the answer: every
 * check, its newest value and when that was read. Read fresh when shown;
 * a check with no reading says so rather than showing a number.
 */
export function FocusCard({ module, onClose }: {
  module: string;
  onClose?: () => void;
}): JSX.Element {
  const detail = useFresh(() => api.moduleDetail(module), [module]);
  const d = detail.data;
  return (
    <aside className="focus-card" aria-label={`${module} status`}>
      <header>
        <span className="name">{d?.module.title ?? module}</span>
        <span className="stamp">
          {detail.fetchedAt ? `read ${detail.fetchedAt.toLocaleTimeString([], { hour12: false })}`
            : detail.loading ? "reading…" : "not read"}
        </span>
        {onClose && (
          <button className="focus-close" onClick={onClose} title="Close and open its screen">
            ×
          </button>
        )}
      </header>
      {detail.error && <p className="missing">Could not read {module}: {detail.error}</p>}
      {d && !d.module.enabled && (
        <p className="missing">Not watched — {d.module.disabled_reason ?? "not loaded"}</p>
      )}
      {d && d.module.enabled && d.checks.length === 0 && (
        <p className="stamp">No checks registered.</p>
      )}
      {d && d.checks.length > 0 && (
        <ul className="focus-checks">
          {d.checks.map((c) => {
            // The server's `current`, never the raw history: a stale OK is
            // Unknown here as on /status and in the spoken check.
            const now = c.current;
            return (
              <li key={c.key} data-health={normaliseHealth(now.health)}>
                <HealthBadge health={now.health} />
                <span className="n">{c.name.replace(/_/g, " ")}</span>
                <span className="v">{now.value !== null ? figure(now.value) : "—"}</span>
                <span className="stamp">
                  {now.at ? `${now.stale ? "stale — last read" : "read"} ${clock(now.at)}`
                    : "never read"}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </aside>
  );
}

/** What the last answer asked to be drawn beside it -- the checklist, or a
 *  module's card. Shared by the overlay and the AI Core page. */
export function AnswerExtras({ voice }: { voice: ReturnType<typeof useVoice> }): JSX.Element | null {
  const card = planFocus(voice.focus).card;
  if (voice.kind === "system_check" && voice.check) {
    return <SystemCheckPanel check={voice.check} />;
  }
  if (card) return <FocusCard module={card} onClose={voice.dismiss} />;
  return null;
}

/** The stage pill -- the same `.hud-status` the AI Core page draws, fed by
 *  the same `voice.status` -- with the mic badge's mute beside it. */
export function StatusPill({ voice }: { voice: ReturnType<typeof useVoice> }): JSX.Element {
  return (
    <span className="voice-pill">
      <span className="hud-status" data-status={voice.status}>
        <span className="dot" aria-hidden="true" />
        {voice.status}
      </span>
      <MuteButton voice={voice} />
    </span>
  );
}

export function VoiceOverlay({ voice, onStage = false }: {
  voice: ReturnType<typeof useVoice>;
  /** The AI Core page is itself the stage: its figure, its pill and its log
   *  are already on screen, so a second, veiled copy on top of it would be
   *  two pills for one state. There the overlay draws nothing -- and still
   *  chimes, because the chime is the wake, not the drawing. */
  onStage?: boolean;
}): JSX.Element | null {
  const { phase } = voice;
  const awake = phase !== "dormant";
  const previous = useRef<Phase>("dormant");

  useEffect(() => {
    if (previous.current === "dormant" && phase === "waking") chime();
    previous.current = phase;
  }, [phase]);

  if (onStage) return null;

  const text = voice.typed;
  const caret = phase === "responding" && text.length <
    (voice.answer || voice.problem || "").length;
  // The exchange in progress. Once answered it moves into the log below.
  const live = phase === "thinking" || phase === "responding";

  return (
    <div className="voice" data-phase={phase} data-awake={String(awake)}>
      {/* Dims the HUD behind without unmounting it, so the SSE stream and
          everything on screen survives the conversation. */}
      <div
        className="voice-veil"
        onClick={voice.dismiss}
        aria-hidden={!awake}
      />

      <div className="voice-stage">
        <div className="core-wrap">
          {/* Four ring layers, spun up at staggered rates. */}
          {[0, 1, 2, 3].map((i) => (
            <span key={i} className={`ring r${i}`} aria-hidden="true" />
          ))}
          <span className="burst" aria-hidden="true" />
          <button
            className="core"
            onClick={voice.tap}
            aria-label={
              phase === "dormant"
                ? coreLabel(voice.detector, voice.detectorClap)
                : tapLabel(phase) ?? "Dismiss"
            }
          >
            <span className="core-dot" aria-hidden="true" />
          </button>
          <SpeakingSparks voice={voice} />
          {phase === "dormant" && (
            <span className="hint">{voiceHint(voice)}</span>
          )}
        </div>

        {awake && (
          <div
            className="voice-body"
            onClick={(e): void => { if (e.target === e.currentTarget) voice.dismiss(); }}
          >
            <div className="voice-status">
              <StatusPill voice={voice} />
              {phase === "waking" && <span className="chip">Wake phrase detected</span>}
            </div>

            {/* Real amplitude. In THINKING it collapses to a line with a
                travelling pulse, which is a different shape, not a fake one. */}
            <div className="wave" data-flat={String(phase === "thinking")}>
              {Array.from({ length: BARS }, (_, i) => (
                <span
                  key={i}
                  className="bar"
                  style={{
                    // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition
                    ["--h" as string]: `${Math.round((voice.bars[i] ?? 0) * 100)}%`,
                    ["--i" as string]: String(i),
                  }}
                />
              ))}
              {phase === "thinking" && <span className="pulse" aria-hidden="true" />}
            </div>

            {live && voice.transcript && (
              <p className="said">“{voice.transcript}”</p>
            )}

            {live && (text || voice.problem) && (
              <p className={`answer${voice.problem && !voice.answer ? " problem" : ""}`}>
                {/* A problem is instructions, not speech: shown whole at once.
                    Typed out, it was cut off mid-sentence by the close. */}
                {voice.problem && !voice.answer ? voice.problem : text}
                {caret && !(voice.problem && !voice.answer) && <span className="caret" aria-hidden="true" />}
              </p>
            )}

            <AnswerExtras voice={voice} />

            <TranscriptLog turns={voice.log} />

            {voice.speechProblem && (
              <p className="missing">{voice.speechProblem}</p>
            )}

            <VoiceHints voice={voice} />
          </div>
        )}
      </div>
    </div>
  );
}
