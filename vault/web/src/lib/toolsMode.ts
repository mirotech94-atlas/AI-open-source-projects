/* Which set of tools the operator wants: everything, or the essentials.
 *
 * This used to be a chip in the header. The owner asked for it to live in
 * settings instead, which is where a preference belongs: the header is for
 * what is happening now, not for how the app is configured.
 *
 * The value stays in `localStorage` under its existing key, so nobody's
 * existing choice is reset by the move. Absent still means advanced: the flag
 * decides which navigation rows exist at all, so a browser with nothing stored
 * must keep the whole tree. Only an explicit "0", written by someone who chose
 * the essentials, means the shorter one.
 *
 * `Shell` owns the React state and `Settings -> Appearance` changes it, and
 * they are not in the same tree, so the change is announced on the window
 * rather than threaded through props. A `CustomEvent` is enough: this is one
 * boolean, changed by a person, a few times ever.
 */

const KEY = "guardian.topbar.advanced";
const EVENT = "guardian:tools-mode";

export function readToolsMode(): boolean {
  try {
    const raw = window.localStorage.getItem(KEY);
    return raw === null ? true : raw === "1";
  } catch {
    // A private window or blocked site data: keep every tool rather than
    // silently hiding navigation the person had yesterday.
    return true;
  }
}

export function setToolsMode(advanced: boolean): void {
  try {
    window.localStorage.setItem(KEY, advanced ? "1" : "0");
  } catch {
    // Unwritable storage still gets the in-session change below; it simply
    // does not survive a reload. Better than refusing the click.
  }
  window.dispatchEvent(new CustomEvent(EVENT, { detail: advanced }));
}

/** Calls back whenever the mode changes, including from another tab. */
export function subscribeToolsMode(onChange: (advanced: boolean) => void): () => void {
  const local = (e: Event) => onChange(Boolean((e as CustomEvent<boolean>).detail));
  const cross = (e: StorageEvent) => {
    if (e.key === KEY) onChange(readToolsMode());
  };
  window.addEventListener(EVENT, local);
  window.addEventListener("storage", cross);
  return () => {
    window.removeEventListener(EVENT, local);
    window.removeEventListener("storage", cross);
  };
}
