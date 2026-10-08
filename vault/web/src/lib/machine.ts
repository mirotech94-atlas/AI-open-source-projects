/**
 * Is this string something a machine emitted, rather than something a person
 * wrote?
 *
 * Vault's typographic rule is that monospace means machine-produced:
 * addresses, MACs, hostnames, versions, timestamps — anything read character
 * by character, where a transposed digit changes the meaning and the reader
 * needs the columns to line up. Prose a human wrote is set in the sans face.
 * Applied consistently it does most of the work of making the product read as
 * an instrument rather than a website.
 *
 * The rule is easy to state and easy to break in one specific place: a field
 * that usually holds a NAME and falls back to an address when there is no
 * name. Three of them exist —
 *
 *   `device.label || device.name || device.mac`     (the device grid)
 *   `row.device || row.client`                       (the DNS query stream)
 *   `row.name`                                       (the DNS count bars)
 *
 * — and each renders "Miro's iPhone" one row and `6e:f8:af:e3:20:61` the next,
 * through the same span. Marking the whole field mono would set every device
 * name in a typewriter face; leaving it sans sets MAC addresses in a
 * proportional one, which is where this was: measured on 2026-09-20, the DNS
 * page had 27 machine values in the sans face against 17 in mono.
 *
 * So the decision is made per value rather than per field.
 *
 * Deliberately conservative. A false positive is worse than a miss here: a
 * device someone named "10.0.0.1" is being sarcastic and can live with
 * monospace, but a vendor called "Cisco Systems" set in a typewriter face is
 * just wrong. Anything with a space in it is prose by definition, and the
 * patterns below match whole strings only.
 */

/** IPv4 dotted quad, with each octet in range — `999.1.1.1` is not an address. */
const IPV4 = /^(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)$/;

/** MAC, colon- or hyphen-separated. */
const MAC = /^(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}$/i;

/** IPv6 only in its compressed form. Requiring `::` keeps this from matching
 *  a clock ("14:22"), which is machine-produced but is already handled where
 *  it is rendered and would otherwise be caught here by accident. */
const IPV6 = /^[0-9a-f]{0,4}(?::[0-9a-f]{0,4}){2,7}$/i;

/** A dotted hostname: `api.guarder.io`, `web-01.local`. Requires at least one
 *  dot and a plausible last label, so a sentence fragment ending in a full
 *  stop does not qualify. */
const HOSTNAME = /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$/i;

/** A version: `2.8.1`, `v1.20`.
 *
 *  Either three or more parts, or an explicit `v`. A bare two-part decimal is
 *  deliberately NOT a version: `3.5` is far more often a rating, a score or a
 *  quantity than a release, and setting those in a typewriter face is exactly
 *  the false positive this module says it would rather miss than make. The
 *  cost is that a real `1.20` with no `v` goes unmarked. */
const VERSION = /^(?:v\d+(?:\.\d+){1,3}|\d+(?:\.\d+){2,3})$/;

export function isMachineValue(value: string): boolean {
  const v = (value ?? "").trim();
  // Prose has spaces; a machine value does not. Cheapest discriminator there
  // is, and it alone rules out almost every human-written name.
  if (v === "" || /\s/.test(v)) return false;
  if (IPV4.test(v) || MAC.test(v) || VERSION.test(v) || HOSTNAME.test(v)) return true;
  return v.includes("::") && IPV6.test(v);
}

/** The class to put on a value that may or may not be machine-produced.
 *  `mono` is the app's existing utility class, so this adds no new styling —
 *  it only decides which values get it. */
export function machineClass(value: string): string {
  return isMachineValue(value) ? "mono" : "";
}
