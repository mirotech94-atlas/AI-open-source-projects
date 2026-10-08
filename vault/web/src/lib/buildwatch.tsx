import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";

/**
 * Notice when the server has deployed a newer build than the one running.
 *
 * A single-page app never reloads itself. Whatever bundle a tab loaded when it
 * was opened is what it runs until somebody reloads the page -- so a tab left
 * open across a deploy keeps showing the old app indefinitely, while a fresh
 * tab beside it shows the new one. That is exactly how the console's redesign
 * shipped and was reported as "still the old design": the server was serving
 * the new bundle to every fresh request and the owner's open tab never asked
 * for it.
 *
 * `deploy.sh` writes `/build.json` with the deployed commit and bakes the same
 * commit into this bundle as `__BUILD_SHA__`. This polls the one and compares
 * it to the other. On a mismatch it reloads on the NEXT navigation -- never
 * mid-page, because a reload under somebody typing into a form is worse than a
 * stale page -- and says so in a line at the top until then, so the reload is
 * one click away for anyone who wants it now.
 *
 * `cache: "no-store"` on the fetch, because a cached answer to "is there a
 * newer build" is the one thing that would make this useless.
 */
const POLL_MS = 60_000;

async function serverBuild(): Promise<string | null> {
  try {
    const r = await fetch("/build.json", { cache: "no-store" });
    if (!r.ok) return null;
    const j = (await r.json()) as { sha?: string };
    return typeof j.sha === "string" && j.sha !== "" ? j.sha : null;
  } catch {
    return null;
  }
}

export function BuildWatch() {
  const [newer, setNewer] = useState(false);
  const { pathname } = useLocation();
  const armed = useRef(false);

  useEffect(() => {
    // A dev bundle has no deployed counterpart to compare against.
    if (__BUILD_SHA__ === "dev") return;
    let stop = false;
    const check = async () => {
      const sha = await serverBuild();
      if (!stop && sha && sha !== __BUILD_SHA__) setNewer(true);
    };
    void check();
    const id = window.setInterval(check, POLL_MS);
    // Coming back to a tab is the moment people are most likely to be looking
    // at something stale, so check then too rather than waiting out the poll.
    const onVisible = () => { if (document.visibilityState === "visible") void check(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => { stop = true; window.clearInterval(id); document.removeEventListener("visibilitychange", onVisible); };
  }, []);

  // Reload on the first navigation AFTER a newer build was seen -- not on the
  // render that noticed it, which is why the flag is armed one tick later.
  useEffect(() => {
    if (!newer) return;
    if (armed.current) { window.location.reload(); return; }
    armed.current = true;
  }, [newer, pathname]);

  if (!newer) return null;
  return (
    <div className="g-newbuild" role="status">
      <span>Vault has been updated since this page was opened.</span>
      <button type="button" className="g-newbuild-btn" onClick={() => window.location.reload()}>
        Reload now
      </button>
    </div>
  );
}
