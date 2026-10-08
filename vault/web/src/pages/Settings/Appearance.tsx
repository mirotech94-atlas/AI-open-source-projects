import { useEffect, useState } from "react";
import { usePreferences } from "../../lib/preferences";



import type { Role } from "../../lib/auth";
import { readToolsMode, setToolsMode, subscribeToolsMode } from "../../lib/toolsMode";
import { useTheme } from "../../lib/theme";
import type { Theme } from "../../lib/theme";
import type { SettingsPayload } from "../../types";

export type AppearanceProps = {
  settings: SettingsPayload;
  role: Role | null;
  onInvalidate: () => void;
};

const OPTIONS: { value: Theme; label: string }[] = [
  { value: "system", label: "System" },
  { value: "light", label: "Calm light" },
  { value: "dark", label: "Console dark" },
];

/** Appearance: the same three-way theme control as the top bar's toggle,
 *  wired to the same `useTheme()` store so picking a skin here updates the
 *  top bar immediately (and vice versa). Everyone can change their own
 *  skin — it is viewer+, not gated — but for an admin the choice also
 *  round-trips through the hub so it survives a new browser; for anyone
 *  else it stays in `localStorage` only, per the hub's own per-role rule. */
export function Appearance(_props: AppearanceProps) {
  const { theme, set } = useTheme();
  const preferences = usePreferences();
  const [tools, setTools] = useState(readToolsMode);
  useEffect(() => subscribeToolsMode(setTools), []);
  function onSelect(next: Theme) {
    set(next);
    preferences.save({...preferences.value, theme: next});
  }

  return (
    <div className="g-settings-appearance">
      {/* Which tools the navigation offers. This was a chip in the header
          until the owner asked for it to move: the header is for what is
          happening now, and which tools you want is a preference. Absent
          means everything, so nobody loses navigation they had yesterday. */}
      <fieldset className="g-settings-theme-picker">
        <legend>Tools</legend>
        <label className="g-settings-theme-option">
          <input
            type="radio"
            name="settings-tools"
            value="advanced"
            checked={tools}
            onChange={() => setToolsMode(true)}
          />
          Everything
        </label>
        <label className="g-settings-theme-option">
          <input
            type="radio"
            name="settings-tools"
            value="basic"
            checked={!tools}
            onChange={() => setToolsMode(false)}
          />
          The essentials
        </label>
        <p className="hint">
          The essentials keeps Overview, Inbox, Assets, Activity, Reports,
          Config and Help. Everything adds topology, history, security,
          updates and the DNS filter. You can change this whenever you like.
        </p>
      </fieldset>

      <fieldset className="g-settings-theme-picker">
        <legend>Skin</legend>
        {OPTIONS.map((opt) => (
          <label key={opt.value} className="g-settings-theme-option">
            <input
              type="radio"
              name="settings-theme"
              value={opt.value}
              checked={theme === opt.value}
              onChange={() => onSelect(opt.value)}
            />
            {opt.label}
          </label>
        ))}
      </fieldset>
      <p className="hint">
        Saved to your personal account, without changing other people's views.
        {preferences.error && <span role="alert"> Could not save; try again.</span>}
      </p>
    </div>
  );
}

export default Appearance;
