/**
 * The dashboard is three tabs (the HUD is not one: it opens when ATLAS is called). Every older path still resolves: it redirects
 * to the tab that now holds what it showed (the pages stay on disk).
 */
export const TABS = [
  { to: "/", label: "Command Center" },
  { to: "/agency", label: "Agency" },
] as const;

export const REDIRECTS: Readonly<Record<string, string>> = {
  "/fleet": "/agency",
  "/agents": "/agency",
  "/agency/room": "/agency",
  "/command": "/",
  "/core": "/",
  "/status": "/",
  "/tasks": "/agency",
  "/calendar": "/agency",
  "/marketing": "/",
  "/leads": "/agency",
  "/activity": "/agency",
  "/reports": "/agency",
  "/workflows": "/",
  "/tools": "/",
  "/knowledge": "/",
  "/memory": "/",
  "/conversations": "/",
};

/** Swipe left goes forward, right goes back, around the four tabs. An
 *  unknown path counts as the first tab. */
export function nextTab(path: string, dir: "left" | "right"): string {
  const at = TABS.findIndex((t) => t.to === path);
  const from = at < 0 ? 0 : at;
  const i = (from + (dir === "left" ? 1 : -1) + TABS.length) % TABS.length;
  return TABS[i]?.to ?? "/";
}
