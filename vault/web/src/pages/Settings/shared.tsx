// Shared bits every Settings section needs: the role-gate copy (a visible
// banner, never just a disabled control with no explanation — the brief's
// "nothing is hidden" rule) and one confirmation dialog used by the kill
// switch, remove-user, revoke-sensor and delete-tenant flows alike.
//
// Not one of the brief's named files, but small and reused by most of
// them — splitting it out avoids five copies of the same two components.

import { useEffect, useId, useRef } from "react";
import type { ReactNode } from "react";
import type { Role } from "../../lib/auth";

// Every write in this area (profile, users, targets, auto-fix, thresholds,
// sensors) is admin-gated per Task 39 — nothing here needs the operator
// tier, so there is only one tooltip constant.
export const ADMIN_TOOLTIP = "admin role required";

const ROLE_LABEL: Record<Role, string> = { viewer: "viewer", operator: "operator", admin: "admin" };

/** The visible explanation shown alongside every disabled control for a
 *  user below the required role — e.g. "Signed in as viewer. Admin access
 *  is required to change these settings." */
function roleNoticeText(role: Role | null, required: Role): string {
  const have = role ? ROLE_LABEL[role] : "viewer";
  const need = ROLE_LABEL[required];
  return `Signed in as ${have}. ${need} access is required to change these settings.`;
}

export function RoleNotice({ role, required }: { role: Role | null; required: Role }) {
  return <p className="g-settings-role-notice">{roleNoticeText(role, required)}</p>;
}

export type ConfirmDialogProps = {
  open: boolean;
  title: string;
  body: ReactNode;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  danger?: boolean;
};

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** A confirmation modal. Same focus-management contract as `Drawer` (see
 *  `web/src/design/Drawer.tsx`) scaled down to a one-shot yes/no: on open,
 *  focus moves to the first button inside the dialog; Tab/Shift-Tab cycle
 *  between the two buttons without ever escaping to the page behind it;
 *  and closing (either button, Escape, or the backdrop) returns focus to
 *  whatever triggered the dialog. */
export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel,
  onConfirm,
  onCancel,
  danger = true,
}: ConfirmDialogProps) {
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement;
    returnFocusRef.current = previous instanceof HTMLElement ? previous : null;
    const dialog = dialogRef.current;
    const first = dialog?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? dialog)?.focus();
    return () => {
      returnFocusRef.current?.focus();
      returnFocusRef.current = null;
    };
  }, [open]);

  if (!open) return null;

  function trapTab(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.key !== "Tab") return;
    const dialog = dialogRef.current;
    if (!dialog) return;
    const items = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (items.length === 0) {
      e.preventDefault();
      dialog.focus();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    } else if (e.shiftKey && (active === first || active === dialog)) {
      e.preventDefault();
      last.focus();
    }
  }

  return (
    <div
      className="g-confirm-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") onCancel();
      }}
    >
      <div
        ref={dialogRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="g-confirm-dialog"
        onKeyDown={trapTab}
      >
        <h3 id={titleId}>{title}</h3>
        <div className="g-confirm-body">{body}</div>
        <div className="g-confirm-actions">
          <button type="button" className="g-confirm-cancel" onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className={danger ? "g-danger" : undefined}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
