import { useEffect, useRef, type JSX } from "react";
import * as THREE from "three";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { buildAndroid } from "./android";
import { faceState, type Gaze } from "./face";
import type { Phase } from "../voice/loop";

/**
 * The humanoid stage: CABLED ATLAS, a dark gunmetal android head under a
 * glowing plexus web, on a neck of cables between armour plates
 * (`android.ts` builds it; this file lights it and moves it).
 *
 * Every motion is either the clock or a real measurement, the rule the
 * particle bust before it kept. The eye glow while SPEAKING is `voice.bars`
 * -- the amplitude actually being read -- and it is the same number that
 * lights the seam between the lips and brightens the face's plexus web. The idle yaw, blink and breath are
 * clock-driven ambient motion that claims to measure nothing, so the only
 * thing that stops them is `prefers-reduced-motion`, read here because a
 * requestAnimationFrame loop is out of a media query's reach.
 *
 * The phases read apart: DORMANT eyes are dim, LISTENING brighter and
 * steady, THINKING a slow amber-tinged breath in the glow, SPEAKING bright
 * and moving with the voice.
 */

/** ±12° of idle yaw: alive, not spinning. */
const YAW = (12 * Math.PI) / 180;

const GLOW: Record<Phase, number> = {
  dormant: 0.55, waking: 1.3, listening: 1.25, thinking: 0.9, responding: 1.35, dismissing: 0.6,
};

/**
 * Fit the camera to the figure: the whole head (crown slot to chin, both
 * ear gears), the cable neck and the shoulder plates. A tall stage has room
 * for the plates out to the deltoids; a short or square one (the 380x300
 * popover) crops just under the collar so the head stays as large as it can.
 */
function fitCamera(camera: THREE.PerspectiveCamera): void {
  const aspect = camera.aspect || 1;
  const top = 2.02;
  const bottom = aspect > 0.95 ? -0.04 : -0.4;
  const halfW = aspect > 0.95 ? 0.62 : 0.9;
  const tanH = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
  const fitH = (top - bottom) / 2 / tanH;
  const fitW = halfW / (tanH * aspect);
  // A little extra for perspective: the face sits well in front of the pivot.
  const distance = Math.max(fitH, fitW) * 1.12;
  const cy = (top + bottom) / 2;
  camera.position.set(0, cy + 0.06, distance);
  camera.lookAt(0, cy, 0);
}

function reducedMotionQuery(): MediaQueryList | null {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-reduced-motion: reduce)")
    : null;
}

export function Bust({ phase, bars, onWake, seed = 20260924, gaze = null }: {
  phase: Phase;
  /** The microphone's real amplitude, 0..1 per bar. Drives the eye glow and
   *  the lip seam while ATLAS is speaking. */
  bars: number[];
  onWake: () => void;
  seed?: number;
  /** Where the cursor or the tracked hand is, normalised 0..1, or null. */
  gaze?: Gaze;
}): JSX.Element {
  const host = useRef<HTMLDivElement | null>(null);
  // Read by the loop rather than closed over, so a phase change never
  // rebuilds the WebGL context.
  const live = useRef({ phase, bars, gaze });
  live.current = { phase, bars, gaze };

  useEffect(() => {
    const mount = host.current;
    if (!mount) return;

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    } catch {
      mount.dataset["failed"] = "true";
      return;
    }
    const pixel = Math.min(2, window.devicePixelRatio || 1);
    renderer.setPixelRatio(pixel);
    renderer.setSize(mount.clientWidth || 1, mount.clientHeight || 1, false);
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 0.82;
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    mount.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    // Reflections: a neutral studio, generated on the device -- no image is
    // fetched, so the CSP stays 'self'.
    const pmrem = new THREE.PMREMGenerator(renderer);
    const room = new RoomEnvironment();
    const envMap = pmrem.fromScene(room, 0.04).texture;
    scene.environment = envMap;

    // Dark satin metal shows mostly what it reflects, so the studio is
    // turned well down: the lights, not the room, do the modelling. A soft
    // cool key from above-left, a faint blue fill from the front (the web's
    // glow on the skin), and two strong cyan rims from behind that trace the
    // silhouette against the dark stage.
    scene.environmentIntensity = 0.7;
    const key = new THREE.DirectionalLight(0xc6d8f0, 1.9);
    key.position.set(-1.8, 2.6, 3);
    const fill = new THREE.DirectionalLight(0x6fa6d8, 0.9);
    fill.position.set(0.4, 0.2, 3);
    const rimR = new THREE.DirectionalLight(0x5fd8ff, 3.4);
    rimR.position.set(2.6, 1.2, -2.4);
    const rimL = new THREE.DirectionalLight(0x4fb8ff, 2.6);
    rimL.position.set(-2.6, 1.4, -2.2);
    scene.add(key, fill, rimR, rimL, new THREE.AmbientLight(0x22303e, 0.5));

    const camera = new THREE.PerspectiveCamera(
      30, (mount.clientWidth || 1) / (mount.clientHeight || 1), 0.1, 100);

    const android = buildAndroid();
    scene.add(android.group);
    const eyeHomeX = android.eyes.map((e) => e.position.x);

    const motion = reducedMotionQuery();
    let still = motion?.matches ?? false;
    const onMotion = (event: MediaQueryListEvent): void => {
      still = event.matches;
    };
    motion?.addEventListener("change", onMotion);
    const started = performance.now();
    let frame = 0;
    let glow = GLOW.dormant;

    const draw = (): void => {
      frame = requestAnimationFrame(draw);
      const t = (performance.now() - started) / 1000;
      const current = live.current.phase;
      const loudest = live.current.bars.reduce(
        (m, b) => (Number.isFinite(b) && b > m ? b : m), 0);
      const speaking = current === "responding";
      const face = faceState({
        phase: current, level: loudest, tMs: performance.now() - started,
        seed, reducedMotion: still, gaze: live.current.gaze,
      });

      // Eased toward the phase's level, plus the voice itself while speaking;
      // thinking breathes slowly instead.
      let target = GLOW[current] + (speaking ? loudest * 1.8 : 0);
      if (current === "thinking" && !still) target += Math.sin(t * 2.4) * 0.25;
      glow += (target - glow) * 0.18;
      android.irises.forEach((m) => {
        m.emissiveIntensity = glow;
        m.emissive.setHex(current === "thinking" ? 0xa9dce8 : 0xcff6ff);
      });
      android.blooms.forEach((b) => {
        (b.material as THREE.SpriteMaterial).opacity = Math.min(1, 0.25 + glow * 0.35);
      });
      // The plexus follows the same glow: dim when dormant, a little
      // brighter with the real voice level, breathing with THINKING.
      android.plexusLines.opacity = Math.min(0.5, 0.14 + glow * 0.26);
      android.plexusNodes.opacity = Math.min(0.9, 0.25 + glow * 0.4);
      android.faceGlow.opacity = Math.min(0.6, 0.12 + glow * 0.24);
      android.lipSeam.emissiveIntensity = speaking ? 0.4 + face.mouthOpen * 2.5 : 0;
      // The lids: `eyeOpen` closes each lens for a blink.
      android.eyes.forEach((e, i) => {
        e.scale.y = Math.max(0.08, face.eyeOpen);
        e.position.x = (eyeHomeX[i] ?? 0) + face.gazeX * 0.004;
      });

      android.group.rotation.y = (still ? 0 : Math.sin(t * 0.26) * YAW) + face.gazeX * 0.06;
      android.group.rotation.z = (face.tiltDeg * Math.PI) / 180;
      // A slight nod toward the viewer, so the crown slot shows.
      android.group.rotation.x = 0.12 - face.gazeY * 0.04;
      android.group.position.y = face.breathY;

      renderer.render(scene, camera);
    };
    frame = requestAnimationFrame(draw);

    const resize = (): void => {
      const w = mount.clientWidth || 1;
      const h = mount.clientHeight || 1;
      camera.aspect = w / h;
      fitCamera(camera);
      camera.updateProjectionMatrix();
      renderer.setSize(w, h, false);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(mount);
    resize();

    return (): void => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      motion?.removeEventListener("change", onMotion);
      android.dispose();
      envMap.dispose();
      pmrem.dispose();
      room.dispose();
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, [seed]);

  return (
    <div
      className="bust"
      ref={host}
      onClick={onWake}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onWake();
        }
      }}
      role="button"
      tabIndex={0}
      aria-label="Cabled ATLAS. Click to talk."
    />
  );
}
