import { useState, type JSX } from "react";
import { api } from "../api";

export function Login({
  configured,
  onSignedIn,
}: {
  configured: boolean;
  onSignedIn: () => void;
}): JSX.Element {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent): Promise<void> => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login(password);
      onSignedIn();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <h1>ATLAS</h1>
      <p>
        Being on the mesh gets you to this page. It does not get you the ability
        to approve anything.
      </p>

      {!configured ? (
        <p className="errorbar">
          No dashboard password is set. Run{" "}
          <code>python -m atlas.cli set-password</code> on the server, put the
          hash in <code>.env</code>, and restart the API.
        </p>
      ) : (
        <form onSubmit={(e) => void submit(e)}>
          {error && <p className="errorbar">{error}</p>}
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoFocus
            required
          />
          <button className="btn btn-approve" type="submit" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
      )}
    </div>
  );
}
