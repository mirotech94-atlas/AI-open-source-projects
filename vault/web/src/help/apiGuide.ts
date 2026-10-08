/**
 * The API guide's content, as data — the shape a generated reference drops
 * into.
 *
 * Vault's public API is being built in a parallel task. **Nothing in this
 * file invents an endpoint**: `API_GUIDE.status` is `"in-progress"` and
 * `groups` is empty, and `pages/ApiGuide.tsx` renders the honest placeholder
 * for exactly that state. That is the point of the file — the section, its
 * nav entry and its layout exist and are reviewed now, so the reference can
 * land as content rather than as a new page.
 *
 * **To fill it in:** replace `API_GUIDE` with a value of the same
 * `ApiGuide` type, `status: "ready"`, and one `ApiGroup` per area of the API.
 * Nothing else in the app has to change: the page reads `API_GUIDE` and
 * renders whatever groups it finds, and `intro`/`auth` are prose slots for the
 * two things every reference needs to say before the first endpoint. A
 * generator may write this file wholesale.
 */

/** One parameter of one endpoint. `where` says how it is carried, so a path
 *  parameter is never mistaken for a query one. */
export type ApiParam = {
  name: string;
  /** `path` | `query` | `header` | `body`. A plain string rather than a union
   *  so a generator emitting a shape this app has not seen renders instead of
   *  failing to type-check. */
  where: string;
  type: string;
  required: boolean;
  description: string;
};

export type ApiEndpoint = {
  /** `GET`, `POST`, `PATCH`, `DELETE`, … — rendered as the method badge. */
  method: string;
  /** The full path, e.g. `/api/public/v1/alerts`. */
  path: string;
  /** One line: what it answers or does. */
  summary: string;
  /** Who may call it — the role or token scope required, in plain words. */
  auth: string;
  params: ApiParam[];
  /** Anything a caller gets wrong without being told: rate limits, paging,
   *  idempotency. One string per note. */
  notes: string[];
};

export type ApiGroup = {
  /** Stable anchor id for deep links: `/help/api#<key>`. */
  key: string;
  title: string;
  summary: string;
  endpoints: ApiEndpoint[];
};

export type ApiGuide = {
  /** `in-progress` renders the placeholder and no endpoint list; `ready`
   *  renders `groups`. */
  status: "in-progress" | "ready";
  /** Prose above the reference: what the API is for, and its base URL. */
  intro: string;
  /** How a caller authenticates. Kept apart from `intro` because it is the
   *  one section every reader needs before anything else works. */
  auth: string;
  groups: ApiGroup[];
};

export const API_GUIDE: ApiGuide = {
  status: "in-progress",
  intro:
    "Vault is getting a public API so you can read your own site's state — alerts, devices, " +
    "scores, reports — from your own scripts and dashboards.",
  auth: "",
  groups: [],
};

/** Total endpoints across every group — the page's own "is there anything to
 *  show?" test, so the placeholder and the reference are never both drawn. */
export function apiEndpointCount(guide: ApiGuide = API_GUIDE): number {
  return guide.groups.reduce((total, group) => total + group.endpoints.length, 0);
}
