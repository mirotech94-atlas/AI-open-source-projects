import { ApiTokenPanel } from "../components/ApiTokenPanel";
export type { ApiTokenRow } from "../components/ApiTokenPanel";

/** API tokens: create, list and revoke the credentials a customer's own code
 *  uses against the public API.
 *
 *  The page is a mount point, nothing more. The panel itself lives in
 *  `components/ApiTokenPanel.tsx` because the Integrations tab shows the same
 *  flow in a quiet, collapsed section -- that is where somebody looking for
 *  "how do I get an API key" actually goes, while this route is reachable only
 *  in advanced mode. Two entrances, one implementation: duplicating a
 *  credential-minting form would mean two places to get the show-once secret,
 *  the role clamp and the revoke confirmation right, and the day they drifted a
 *  customer would find out before we did.
 *
 *  Reachable at `/settings/api-tokens` (Config ▸ API tokens, advanced). */
export default function ApiTokens() {
  return <ApiTokenPanel chrome="cards" />;
}
