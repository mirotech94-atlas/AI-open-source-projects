// One inline SVG glyph per `TopoNode.icon` key. Every key the hub's topology
// builder can hand back (`hub/guardian_hub/payloads/topology.py`) is covered:
// the twelve `kind_of()` device kinds it ever assigns as `icon` directly
// (router, extender, camera, printer, phone, tablet, laptop, tv, speaker,
// appliance, plug, server — plus its `unknown` fallback), and the three
// non-device node kinds (`internet`, plus `server`/`site` for host/site
// nodes, already covered by `server` above). Drawn stroke-only in
// `currentColor` so a node's tone (status ring, risk, untrusted red) tints
// the icon along with its ring, no separate fill color to keep in sync.
import type { ReactElement, SVGProps } from "react";

export type DeviceIconKey =
  | "internet"
  | "router"
  | "extender"
  | "camera"
  | "printer"
  | "phone"
  | "tablet"
  | "laptop"
  | "tv"
  | "speaker"
  | "appliance"
  | "plug"
  | "server"
  | "site"
  | "unknown";

export type DeviceIconProps = {
  icon: string;
  size?: number;
} & Omit<SVGProps<SVGSVGElement>, "width" | "height" | "viewBox">;

const GLYPHS: Record<DeviceIconKey, () => ReactElement> = {
  internet: () => (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18M12 3a13 13 0 0 1 0 18M12 3a13 13 0 0 0 0 18" />
    </>
  ),
  router: () => (
    <>
      <rect x="3" y="10" width="18" height="7" rx="1.5" />
      <path d="M7 10V7a2 2 0 0 1 2-2M17 10V7a2 2 0 0 0-2-2M7 17v3M11 17v3M15 17v3" />
      <circle cx="17" cy="13.5" r="0.6" fill="currentColor" stroke="none" />
    </>
  ),
  extender: () => (
    <>
      <rect x="8" y="4" width="8" height="14" rx="2" />
      <path d="M6 8c-2 1.5-2 6.5 0 8M18 8c2 1.5 2 6.5 0 8M12 14v4" />
    </>
  ),
  camera: () => (
    <>
      <rect x="3" y="7" width="13" height="10" rx="2" />
      <path d="M16 10.5 21 8v8l-5-2.5" />
      <circle cx="9.5" cy="12" r="2.4" />
    </>
  ),
  printer: () => (
    <>
      <path d="M6 8V4h12v4" />
      <rect x="3" y="8" width="18" height="8" rx="1.5" />
      <path d="M6 16v4h12v-4" />
      <circle cx="17" cy="11.5" r="0.6" fill="currentColor" stroke="none" />
    </>
  ),
  phone: () => (
    <>
      <rect x="7" y="2" width="10" height="20" rx="2.5" />
      <path d="M11 19h2" />
    </>
  ),
  tablet: () => (
    <>
      <rect x="4" y="3" width="16" height="18" rx="2.5" />
      <path d="M11 19h2" />
    </>
  ),
  laptop: () => (
    <>
      <rect x="4" y="4" width="16" height="11" rx="1.5" />
      <path d="M2 19h20l-2-3H4l-2 3Z" />
    </>
  ),
  tv: () => (
    <>
      <rect x="3" y="4" width="18" height="12" rx="1.5" />
      <path d="M9 20h6M12 16v4" />
    </>
  ),
  speaker: () => (
    <>
      <rect x="7" y="2" width="10" height="20" rx="2.5" />
      <circle cx="12" cy="8" r="2" />
      <circle cx="12" cy="15" r="3" />
    </>
  ),
  appliance: () => (
    <>
      <rect x="5" y="2" width="14" height="20" rx="1.5" />
      <path d="M5 9h14" />
      <circle cx="9" cy="6" r="0.6" fill="currentColor" stroke="none" />
      <circle cx="9" cy="15" r="2.2" />
    </>
  ),
  plug: () => (
    <>
      <path d="M9 2v6M15 2v6M7 8h10v3a5 5 0 0 1-10 0V8Z" />
      <path d="M12 16v6" />
    </>
  ),
  server: () => (
    <>
      <rect x="4" y="3" width="16" height="6" rx="1.2" />
      <rect x="4" y="10.5" width="16" height="6" rx="1.2" />
      <rect x="4" y="18" width="16" height="3" rx="1" />
      <circle cx="8" cy="6" r="0.6" fill="currentColor" stroke="none" />
      <circle cx="8" cy="13.5" r="0.6" fill="currentColor" stroke="none" />
    </>
  ),
  site: () => (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 3v18M4 8h16M4 16h16" />
    </>
  ),
  unknown: () => (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M9.5 9.2a2.5 2.5 0 1 1 3.6 2.6c-.9.5-1.1.9-1.1 1.7" />
      <circle cx="12" cy="16.3" r="0.6" fill="currentColor" stroke="none" />
    </>
  ),
};

function isKnownIcon(icon: string): icon is DeviceIconKey {
  return icon in GLYPHS;
}

/** Renders `icon`'s glyph, falling back to `unknown` for any key this build
 *  does not recognize — a future hub sending a new device kind must degrade
 *  to a generic mark, not throw. */
export function DeviceIcon({ icon, size = 20, ...rest }: DeviceIconProps) {
  const key = isKnownIcon(icon) ? icon : "unknown";
  const Glyph = GLYPHS[key];
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      {Glyph()}
    </svg>
  );
}

export default DeviceIcon;
