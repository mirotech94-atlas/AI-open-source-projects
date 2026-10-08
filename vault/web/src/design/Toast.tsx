import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
import type { Tone } from "../lib/format";

export type Toast = {
  id: string;
  msg: ReactNode;
  tone: Tone;
};

export type ToastApi = {
  /** Returns the new toast's id, so a caller can dismiss it early. */
  show: (msg: ReactNode, tone?: Tone) => string;
  dismiss: (id: string) => void;
};

const AUTO_DISMISS_MS = 6000;

const ToastContext = createContext<ToastApi | null>(null);

export function ToastProvider({ children }: { children?: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const seq = useRef(0);

  const dismiss = useCallback((id: string) => {
    const timer = timers.current.get(id);
    if (timer !== undefined) {
      clearTimeout(timer);
      timers.current.delete(id);
    }
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const show = useCallback(
    (msg: ReactNode, tone: Tone = "plain") => {
      seq.current += 1;
      const id = `t${seq.current}`;
      setToasts((prev) => [...prev, { id, msg, tone }]);
      timers.current.set(
        id,
        setTimeout(() => {
          timers.current.delete(id);
          setToasts((prev) => prev.filter((t) => t.id !== id));
        }, AUTO_DISMISS_MS),
      );
      return id;
    },
    [],
  );

  // React 19 StrictMode mounts twice in development; every pending timer has to
  // be cleared on unmount or a toast fires against a dead tree.
  useEffect(() => {
    const map = timers.current;
    return () => {
      for (const timer of map.values()) clearTimeout(timer);
      map.clear();
    };
  }, []);

  const api = useMemo<ToastApi>(() => ({ show, dismiss }), [show, dismiss]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="g-toasts">
        {toasts.map((t) => (
          <div key={t.id} className={`g-toast g-tone-${t.tone}`} role="status">
            <span>{t.msg}</span>
            <button
              type="button"
              className="g-toast-close"
              aria-label="Dismiss"
              onClick={() => dismiss(t.id)}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

/** Throws when used outside `ToastProvider` — a silently swallowed toast hides
 *  exactly the failures it exists to report. */
// The provider and its hook belong in one file — the brief pairs them, and a
// hook exported next to a component only costs Fast Refresh granularity in dev.
// oxlint-disable-next-line react/only-export-components
export function useToast(): ToastApi {
  const api = useContext(ToastContext);
  if (api === null) {
    throw new Error("useToast must be used inside a <ToastProvider>");
  }
  return api;
}

export default ToastProvider;
