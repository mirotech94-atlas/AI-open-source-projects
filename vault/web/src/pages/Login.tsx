import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ApiError, authStart, authVerify } from "../api";
import Logo from "../Logo";

type Step = "username" | "code";

// `?next=` is set by the API transport's 401 redirect (see `lib/api.ts`) so a
// deep link survives the round trip through login. Only a same-origin
// relative path is honoured: a bare `/...` (single leading slash, so
// `//evil.example` -- a protocol-relative URL -- is refused) rather than an
// absolute URL, which would send a signed-in user off the site.
function safeNextPath(raw: string | null): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//")) return "/";
  return raw;
}

export default function Login() {
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>("username");
  const [username, setUsername] = useState("");
  const [code, setCode] = useState("");
  const [remember, setRemember] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // `/login?u=<username>` is the link in the "your account is ready" email:
  // pre-fill the username and request the code straight away, so the person
  // goes email -> click -> code -> in. Once only, even under StrictMode.
  const [params] = useSearchParams();
  const prefill = (params.get("u") ?? "").trim();
  const started = useRef(false);
  useEffect(() => {
    if (!prefill || started.current) return;
    started.current = true;
    setUsername(prefill);
    setBusy(true);
    authStart(prefill)
      .then(() => setStep("code"))
      .catch((err) => setError(err instanceof ApiError ? err.message : "Could not send the code. Try again."))
      .finally(() => setBusy(false));
  }, [prefill]);

  async function handleStart(e: FormEvent) {
    e.preventDefault();
    const trimmed = username.trim();
    if (!trimmed) return;
    setBusy(true);
    setError(null);
    try {
      await authStart(trimmed);
      setStep("code");
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Could not send the code. Try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function handleVerify(e: FormEvent) {
    e.preventDefault();
    if (code.trim().length !== 6) return;
    setBusy(true);
    setError(null);
    try {
      await authVerify(username.trim(), code.trim(), remember);
      navigate(safeNextPath(params.get("next")), { replace: true });
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401
          ? "That code is wrong or expired."
          : "Could not verify. Try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  function backToUsername() {
    setStep("username");
    setCode("");
    setError(null);
  }

  // One tag, once, and it cleans up after itself so a remount during dev does
  // not stack a second WebGL context on the same canvas.
  useEffect(() => {
    const tag = document.createElement("script");
    tag.src = "/auth-lattice.js";
    tag.defer = true;
    document.body.appendChild(tag);
    return () => { tag.remove(); };
  }, []);

  return (
    <div className="auth-shell">
      {/* The reference's split sign-in: the pitch on the left on the rail's
          darker ground, the form on the right. The lattice lives behind the
          pitch now -- still the only motion on the screen, still decorative
          and hidden from assistive technology. The script is loaded here
          rather than in index.html because this is the only route that uses
          it (see public/auth-lattice.js). */}
      <aside className="auth-side">
        <canvas id="auth-gl" aria-hidden="true" />
        <div id="auth-fade" aria-hidden="true" />
        <div className="auth-side-in">
          <div className="auth-brand">
            <Logo size={22} />
            <span>Vault</span>
          </div>
          <div className="auth-pitch">
            <h1>One sensor. Every way into your network, watched.</h1>
            <p>
              Vault finds the devices, servers, websites, certificates, DNS and open
              ports an attacker looks for first — then closes what it safely can from an
              allow-listed set of actions, and logs every one. Anything disruptive waits
              for you.
            </p>
          </div>
          <p className="auth-foot">guarder.io · built for whoever gets the call</p>
        </div>
      </aside>
      <main className="auth-main">
      <div className="auth-card">
        <h2 className="auth-h2">Sign in</h2>
        <p className="auth-tagline">A six-digit code is emailed to you. There is no password to remember.</p>

        {step === "username" ? (
          <form onSubmit={handleStart}>
            <label htmlFor="username">Username</label>
            <input
              id="username"
              autoFocus
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="you"
            />
            {error && <p className="auth-error">{error}</p>}
            <button type="submit" disabled={busy || !username.trim()}>
              {busy ? "Sending..." : "Send code"}
            </button>
          </form>
        ) : (
          <form onSubmit={handleVerify}>
            <p className="auth-hint">We emailed a code to {username}.</p>
            <label htmlFor="code">6-digit code</label>
            <input
              id="code"
              autoFocus
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              placeholder="000000"
              className="mono"
            />
            <label className="auth-remember" htmlFor="remember">
              <input
                id="remember"
                type="checkbox"
                checked={remember}
                onChange={(e) => setRemember(e.target.checked)}
              />
              Remember this device
            </label>
            {error && <p className="auth-error">{error}</p>}
            <button type="submit" disabled={busy || code.trim().length !== 6}>
              {busy ? "Verifying..." : "Verify"}
            </button>
            <button
              type="button"
              className="auth-secondary"
              onClick={backToUsername}
            >
              Use a different username
            </button>
          </form>
        )}
        <p className="auth-note">
          <span className="auth-note-dot" aria-hidden="true" />
          Nothing is kept on this device unless you ask it to remember you.
        </p>
        <p className="auth-fine">A session lasts 12 hours. A remembered device stays signed in for 30 days.</p>
      </div>
      </main>
    </div>
  );
}
