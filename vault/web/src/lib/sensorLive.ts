import type { SensorRow } from "../types";

/** How recently a sensor must have reported to count as online. Matches the
 *  hub's `payloads/overview.SENSOR_LIVE_S`, which draws the Overview's
 *  sensor state, so the two pages cannot disagree about the same sensor. */
export const SENSOR_LIVE_S = 900;

/** Is this sensor online right now?
 *
 *  `status` is the ENROLMENT state (pending | active | revoked), not
 *  liveness. The Settings tile used to count `status === "online"`, a value
 *  the hub never sends, so every customer read "0 online" in warning colour
 *  next to a healthy sensor. Liveness is how recently it reported. */
export function sensorIsLive(s: Pick<SensorRow, "status" | "age_s">): boolean {
  return s.status === "active" && s.age_s !== null && s.age_s !== undefined && s.age_s < SENSOR_LIVE_S;
}
