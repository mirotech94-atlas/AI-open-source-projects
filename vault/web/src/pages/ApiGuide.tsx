import { Link } from "react-router-dom";
import { Card } from "../design";
import { API_GUIDE, apiEndpointCount } from "../help/apiGuide";
import type { ApiEndpoint, ApiGroup } from "../help/apiGuide";
import "../help/help.css";

function Endpoint({ endpoint }: { endpoint: ApiEndpoint }) {
  return (
    <article className="g-api-endpoint">
      <h3 className="g-api-endpoint-head">
        <span className="g-api-method mono">{endpoint.method}</span>
        <code className="g-api-path">{endpoint.path}</code>
      </h3>
      <p className="g-help-p">{endpoint.summary}</p>
      <p className="hint">Requires: {endpoint.auth}</p>
      {endpoint.params.length > 0 && (
        <ul className="g-help-bullets">
          {endpoint.params.map((param) => (
            <li key={`${param.where}:${param.name}`}>
              <code className="mono">{param.name}</code> ({param.where}, {param.type}
              {param.required ? ", required" : ""}) — {param.description}
            </li>
          ))}
        </ul>
      )}
      {endpoint.notes.map((note) => (
        <p key={note} className="g-help-note">
          <span className="g-help-note-label">Worth knowing</span>
          {note}
        </p>
      ))}
    </article>
  );
}

function Group({ group }: { group: ApiGroup }) {
  return (
    <section className="g-api-group" id={group.key}>
      <h2>{group.title}</h2>
      <p className="g-help-summary">{group.summary}</p>
      {group.endpoints.map((endpoint) => (
        <Endpoint key={`${endpoint.method} ${endpoint.path}`} endpoint={endpoint} />
      ))}
    </section>
  );
}

/**
 * The API guide.
 *
 * Vault's public API is being built in a parallel task, so today this page
 * renders one honest "in progress" panel and nothing else. It is deliberately
 * a real page with a real route and a real nav entry rather than a stub added
 * later: the reference is *content*, and it drops into `help/apiGuide.ts` —
 * flip `API_GUIDE.status` to `"ready"` and fill `groups`, and everything below
 * renders it with no change here. See that file's header for the contract.
 */
export function ApiGuide() {
  const ready = API_GUIDE.status === "ready" && apiEndpointCount() > 0;

  return (
    <div className="g-help-article">
      <p className="g-help-crumb">
        <Link to="/help">All guides</Link>
      </p>
      <h1>API guide</h1>
      <p className="g-help-summary">{API_GUIDE.intro}</p>

      {!ready ? (
        <Card title="The API reference is still being written" tone="warn">
          <p className="g-help-p">
            There is nothing to document yet, and we would rather show you this than a list of
            endpoints that do not exist. When the API ships, its full reference — every path,
            what it needs, and what it returns — appears on this page.
          </p>
          <p className="g-help-p">
            In the meantime, everything the API will expose is already readable in the app, and
            Vault's chat can answer questions about your site directly.
          </p>
          <p className="g-help-where">
            <Link to="/help">Help guides</Link>
            <span className="hint"> — how the app itself works</span>
          </p>
        </Card>
      ) : (
        <>
          {API_GUIDE.auth !== "" && (
            <section className="g-api-group" id="auth">
              <h2>Authentication</h2>
              <p className="g-help-p">{API_GUIDE.auth}</p>
            </section>
          )}
          {API_GUIDE.groups.map((group) => (
            <Group key={group.key} group={group} />
          ))}
        </>
      )}
    </div>
  );
}

export default ApiGuide;
