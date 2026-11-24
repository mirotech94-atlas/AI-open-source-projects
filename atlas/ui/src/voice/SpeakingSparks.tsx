import { useEffect, useRef, useState, type JSX } from "react";
import { SILENT_LEVEL, SparkField, type Origin } from "./sparks";
import type { useVoice } from "./useVoice";

/**
 * Sparks in the air while ATLAS speaks: green and teal particles rising and
 * drifting from the core, as many as his voice is loud (`voice.outputLevel`,
 * the analyser on the clip that is playing) and none when it is quiet.
 *
 * Canvas 2D only, no dependency. With `prefers-reduced-motion` nothing moves:
 * the particles are not drawn and a calm, slow glow shows that he is speaking
 * instead (`.spark-calm`, opacity only).
 *
 * The canvas fills its parent and never takes a click. `origin` is the core's
 * centre and radius as fractions of the canvas (radius of its shorter side).
 */

const GREEN = [46, 229, 157];
const TEAL = [20, 209, 192];

function reducedMotion(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function SpeakingSparks({ voice, origin = { x: 0.5, y: 0.5, r: 0.2 },
  className = "" }: {
  voice: ReturnType<typeof useVoice>;
  origin?: Origin;
  className?: string;
}): JSX.Element {
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const speaking = voice.phase === "responding";
  const [calm, setCalm] = useState<boolean>(reducedMotion);
  const read = useRef(voice.outputLevel);
  read.current = voice.outputLevel;
  const talking = useRef(speaking);
  talking.current = speaking;
  const where = useRef(origin);
  where.current = origin;
  /** Starts the frame loop if it is idle; set by the drawing effect. */
  const kick = useRef<(() => void) | null>(null);

  // A change in the OS setting takes effect without a reload.
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const on = (): void => setCalm(query.matches);
    query.addEventListener("change", on);
    return (): void => query.removeEventListener("change", on);
  }, []);

  useEffect(() => {
    const element = canvas.current;
    if (!element || calm) return;
    const context = element.getContext("2d");
    if (!context) return;
    const field = new SparkField();
    let frame = 0;
    let last = performance.now();
    let width = 0;
    let height = 0;

    const fit = (): void => {
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      width = element.clientWidth;
      height = element.clientHeight;
      element.width = Math.max(1, Math.round(width * ratio));
      element.height = Math.max(1, Math.round(height * ratio));
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
    };
    fit();
    const observer = typeof ResizeObserver === "function"
      ? new ResizeObserver(fit) : null;
    observer?.observe(element);

    // The loop runs only while he speaks or sparks are still in the air: a
    // dormant HUD costs no frames (`kick` restarts it when he speaks again).
    const tick = (now: number): void => {
      frame = 0;
      const dt = (now - last) / 1000;
      last = now;
      // The level is read only while he is speaking; otherwise it is 0 and
      // the sparks already in the air burn out.
      const level = talking.current ? read.current() : 0;
      const o = where.current;
      const size = Math.min(width, height);
      field.step(dt, level, { x: o.x * width, y: o.y * height, r: o.r * size });

      context.clearRect(0, 0, width, height);
      if (field.sparks.length === 0) {
        element.dataset.live = "false";
        element.dataset.count = "0";
        if (talking.current) frame = window.requestAnimationFrame(tick);
        return;
      }
      context.globalCompositeOperation = "lighter";
      for (const s of field.sparks) {
        const fade = 1 - s.age / s.life;
        const rgb = s.hue < 0.5 ? GREEN : TEAL;
        context.globalAlpha = Math.max(0, fade) * 0.9;
        context.shadowBlur = 8;
        context.shadowColor = `rgb(${rgb.join(",")})`;
        context.fillStyle = `rgb(${rgb.join(",")})`;
        context.beginPath();
        context.arc(s.x, s.y, s.size * (0.5 + fade * 0.5), 0, Math.PI * 2);
        context.fill();
      }
      context.globalAlpha = 1;
      element.dataset.live = String(field.sparks.length > 0 && level >= SILENT_LEVEL);
      element.dataset.count = String(field.sparks.length);
      frame = window.requestAnimationFrame(tick);
    };
    const start = (): void => {
      if (frame !== 0) return;
      last = performance.now();
      frame = window.requestAnimationFrame(tick);
    };
    kick.current = start;
    if (talking.current) start();

    return (): void => {
      kick.current = null;
      window.cancelAnimationFrame(frame);
      observer?.disconnect();
      field.clear();
      context.clearRect(0, 0, width, height);
    };
  }, [calm]);

  useEffect(() => {
    if (speaking) kick.current?.();
  }, [speaking]);

  if (calm) {
    // No particles: a slow glow while he speaks, nothing when he does not.
    return (
      <span className={`spark-calm ${className}`.trim()} data-speaking={String(speaking)}
            aria-hidden="true" />
    );
  }
  return (
    <canvas ref={canvas} className={`sparks ${className}`.trim()}
            data-speaking={String(speaking)} aria-hidden="true" />
  );
}
