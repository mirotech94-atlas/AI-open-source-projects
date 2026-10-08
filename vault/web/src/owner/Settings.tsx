import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "../design";
import { platformSettings, savePlatformSetting } from "../lib/api";
import type { PlatformSettingField } from "../types";

/**
 * Platform settings.
 *
 * Every field here is read by the product through one accessor, so saving it
 * changes behaviour immediately — no deploy, no restart. That is the whole
 * reason the page exists, and the reason it is short: a setting that looks
 * editable and is read once at boot is worse than one that is read-only,
 * because you watch it save and nothing happens.
 *
 * Credentials are reported, never edited. They live in the environment, where
 * they are not in a database that gets backed up and copied around.
 */
/** Is this stored setting value ON?
 *
 * Mirrors `platform_settings.set_value`'s own bool branch exactly --
 * `str(raw).strip().lower() in ("1", "true", "yes", "on")` -- rather than
 * testing for one spelling. The hub serialises a stored bool with Python's
 * `str()`, which yields "True" with a capital T, so a `=== "true"` comparison
 * renders a switch that is ON as OFF. On this particular setting that meant
 * the console reporting that Vault was not repairing while it was.
 *
 * Agreeing with the writer's parser by construction is the fix; matching one
 * casing is how it broke.
 */
function isOn(value: string): boolean {
  return ["1", "true", "yes", "on"].includes(String(value).trim().toLowerCase());
}

function Field({ field, onSave, saving, error }: {
  field: PlatformSettingField;
  onSave: (value: string) => void;
  saving: boolean;
  error: string;
}) {
  const [draft, setDraft] = useState(field.value);
  const dirty = draft !== field.value;

  return (
    <div className="g-set-row">
      <div className="g-set-copy">
        <label htmlFor={`set-${field.key}`}>{field.label}</label>
        <p>{field.help}</p>
        {field.overridden && (
          <p className="g-set-origin">
            {`Changed from the default of ${field.default}${field.updated_by ? ` by ${field.updated_by}` : ""}.`}
          </p>
        )}
      </div>
      <div className="g-set-control">
        {/* A boolean is a switch, not a field you type "true" into. Without
            this branch it fell through to the text input below, which on the
            one setting here that is a kill switch would have meant typing a
            word at a control that stops Vault repairing anything. */}
        {field.kind === "bool" ? (
          <button
            id={`set-${field.key}`}
            type="button"
            role="switch"
            aria-checked={isOn(draft)}
            className={`g-set-toggle${isOn(draft) ? " is-on" : ""}`}
            disabled={saving}
            // Saves on the flip. A switch with a separate Save beside it
            // invites the state where the control says one thing and the
            // platform is doing another, which on this particular setting is
            // the difference between "Vault is repairing" and "Vault is
            // not" -- the exact thing an owner came here to be sure of.
            onClick={() => { const next = isOn(draft) ? "false" : "true"; setDraft(next); onSave(next); }}
          >
            <span className="g-set-toggle-knob" />
          </button>
        ) : field.kind === "choice" ? (
          <select id={`set-${field.key}`} value={draft} onChange={(e) => setDraft(e.target.value)}>
            {field.choices.map((c) => (
              <option key={c} value={c}>{c.replace(/_/g, " ")}</option>
            ))}
          </select>
        ) : (
          <input
            id={`set-${field.key}`}
            type={field.kind === "int" ? "number" : "text"}
            inputMode={field.kind === "int" ? "numeric" : undefined}
            min={field.minimum ?? undefined}
            max={field.maximum ?? undefined}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
        )}
        {field.kind !== "bool" && (
          <button
            type="button"
            className="g-btn-primary"
            disabled={!dirty || saving}
            onClick={() => onSave(draft)}
          >
            {saving ? "Saving…" : "Save"}
          </button>
        )}
        {error && <p className="g-set-err" role="status">{error}</p>}
      </div>
    </div>
  );
}

export function OwnerSettings() {
  const qc = useQueryClient();
  const query = useQuery({ queryKey: ["platform-settings"], queryFn: platformSettings });
  const [pending, setPending] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});

  const save = useMutation({
    mutationFn: ({ key, value }: { key: string; value: string }) => savePlatformSetting(key, value),
    onMutate: ({ key }) => { setPending(key); setErrors((e) => ({ ...e, [key]: "" })); },
    onSuccess: (payload) => {
      qc.setQueryData(["platform-settings"], payload);
      // Anything that reads a changed setting is now stale.
      qc.invalidateQueries({ queryKey: ["platform"] });
      qc.invalidateQueries({ queryKey: ["billing"] });
    },
    onError: (err: Error, vars) => setErrors((e) => ({ ...e, [vars.key]: err.message })),
    onSettled: () => setPending(null),
  });

  const fields = query.data?.fields ?? [];
  const groups = [...new Set(fields.map((f) => f.group))];

  return (
    <section className="g-set" data-testid="owner-settings">
      <header className="g-set-head">
        <p className="g-set-sub">
          {query.isError
            ? "These could not be read."
            : "Changes take effect immediately. Nothing here needs a deploy."}
        </p>
      </header>

      {groups.map((group) => (
        <div key={group} className="g-set-group">
          <h2>{group}</h2>
          {fields.filter((f) => f.group === group).map((f) => (
            <Field
              key={f.key}
              field={f}
              saving={pending === f.key}
              error={errors[f.key] ?? ""}
              onSave={(value) => save.mutate({ key: f.key, value })}
            />
          ))}
        </div>
      ))}

      <div className="g-set-group">
        <h2>Credentials</h2>
        <p className="g-set-sub">
          Set in the hub&rsquo;s environment, not here. A key kept in the database would travel
          with every backup and export of it, so this page only reports whether each one is
          configured.
        </p>
        <ul className="g-set-secrets">
          {(query.data?.secrets ?? []).map((s) => (
            <li key={s.key}>
              <span>{s.label}</span>
              <Badge tone={s.set ? "good" : "warn"}>{s.set ? "set" : "not set"}</Badge>
            </li>
          ))}
        </ul>
      </div>

      {query.data && (
        <p className="g-set-foot">
          Vault {query.data.version} · {query.data.base_url}
        </p>
      )}
    </section>
  );
}

export default OwnerSettings;
