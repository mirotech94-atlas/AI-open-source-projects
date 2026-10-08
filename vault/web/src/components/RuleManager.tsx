import { useState } from "react";
import type { DnsList, DnsRules } from "../types";

export type RuleKind = "allow" | "block";

export type RuleManagerProps = {
  rules: DnsRules;
  lists: DnsList[];
  onAdd: (kind: RuleKind, domain: string) => void;
  onRemove: (kind: RuleKind, domain: string) => void;
  onToggleList: (name: string, enabled: boolean) => void;
  canWrite?: boolean;
  writeTooltip?: string;
};

type RuleColumnProps = {
  kind: RuleKind;
  label: string;
  domains: string[];
  onAdd: (kind: RuleKind, domain: string) => void;
  onRemove: (kind: RuleKind, domain: string) => void;
  canWrite: boolean;
  writeTooltip?: string;
};

function RuleColumn({ kind, label, domains, onAdd, onRemove, canWrite, writeTooltip }: RuleColumnProps) {
  const [draft, setDraft] = useState("");
  const fieldLabel = `${label} a domain`;

  function submit() {
    const domain = draft.trim();
    if (domain === "") return;
    onAdd(kind, domain);
    setDraft("");
  }

  return (
    <div className="g-dns-rule-col">
      <h3>{label}</h3>
      <label className="g-dns-rule-add">
        <span>{fieldLabel}</span>
        <input
          type="text"
          aria-label={fieldLabel}
          value={draft}
          disabled={!canWrite}
          title={canWrite ? undefined : writeTooltip}
          placeholder="example.com"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              submit();
            }
          }}
        />
      </label>
      {domains.length === 0 ? (
        <p className="g-asset-empty">No {label.toLowerCase()} rules yet.</p>
      ) : (
        <ul className="g-dns-rule-list">
          {domains.map((d) => (
            <li key={d}>
              {/* A rule domain is a string the reader typed, but this
                  column also renders subject-alt names a lookup surfaced
                  from the wild — long enough, or ugly enough, to break a
                  flex row that never told it to shrink. Truncate
                  deliberately, keep the full value one hover away. */}
              <span className="mono g-dns-rule-domain" title={d}>
                {d}
              </span>
              <button
                type="button"
                disabled={!canWrite}
                title={canWrite ? undefined : writeTooltip}
                onClick={() => onRemove(kind, d)}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Allow/block domain lists plus the subscribed block lists, each with its
 *  entry count and how much it blocked in the last 24h. Domain add/remove
 *  writes through `POST /api/v1/dns/rules` (one rule at a time); a list
 *  toggle writes through the generic `POST /api/v1/config` path (the same
 *  route `dns.lists` settings already use). */
export function RuleManager({
  rules,
  lists,
  onAdd,
  onRemove,
  onToggleList,
  canWrite = true,
  writeTooltip = "admin role required",
}: RuleManagerProps) {
  return (
    <div className="g-dns-rules">
      <div className="g-dns-rule-cols">
        <RuleColumn
          kind="allow"
          label="Allow"
          domains={rules.allow}
          onAdd={onAdd}
          onRemove={onRemove}
          canWrite={canWrite}
          writeTooltip={writeTooltip}
        />
        <RuleColumn
          kind="block"
          label="Block"
          domains={rules.block}
          onAdd={onAdd}
          onRemove={onRemove}
          canWrite={canWrite}
          writeTooltip={writeTooltip}
        />
      </div>

      <div className="g-dns-lists">
        <h3>Block lists</h3>
        {lists.length === 0 ? (
          <p className="g-asset-empty">No block lists subscribed.</p>
        ) : (
          <ul className="g-dns-lists-rows">
            {lists.map((list) => (
              <li key={list.name} className="g-dns-list-row" data-testid="dns-list-row">
                <div className="g-dns-list-info">
                  <span className="g-dns-list-name">{list.name}</span>
                  <span className="g-dns-list-category">{list.category}</span>
                </div>
                <span className="g-dns-list-count">
                  <span className="num">{list.entries.toLocaleString()}</span> entries
                </span>
                <span className="g-dns-list-count">
                  <span className="num">{list.blocked_24h.toLocaleString()}</span> blocked/24h
                </span>
                <button
                  type="button"
                  className={list.enabled ? "g-toggle-on" : ""}
                  aria-pressed={list.enabled}
                  disabled={!canWrite}
                  title={canWrite ? undefined : writeTooltip}
                  onClick={() => onToggleList(list.name, !list.enabled)}
                >
                  {list.enabled ? "Enabled" : "Disabled"}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

export default RuleManager;
