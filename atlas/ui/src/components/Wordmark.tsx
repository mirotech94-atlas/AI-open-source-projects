import type { JSX } from "react";
import { BRAND } from "../theme";

/**
 * The ATLAS Agency logotype (2026-10-29): a small royal-blue mark, "ATLAS"
 * heavy and tracked, "Agency" lighter beside it (set in capitals by CSS).
 * Styles: `.atlas-wordmark` in command.css. The mark is decoration; the
 * words carry the name, so a screen reader reads "ATLAS Agency".
 */
export function Wordmark({ size = 18 }: { size?: number }): JSX.Element {
  return (
    <span className="atlas-wordmark">
      <svg className="aw-mark" viewBox="0 0 12 12" width={size * 0.6} height={size * 0.6}
           aria-hidden="true" focusable="false">
        <path d="M6 0.5 11.5 11.5H0.5Z" fill={BRAND.royal} />
      </svg>
      <b className="aw-atlas">ATLAS</b>{" "}
      <span className="aw-agency">Agency</span>
    </span>
  );
}
