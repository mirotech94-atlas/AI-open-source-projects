import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Table, useToast } from "../../design";
import type { Column } from "../../design";
import { removeUser, saveSettingsSection } from "../../lib/api";
import { atLeast } from "../../lib/auth";
import type { Role } from "../../lib/auth";
import type { SettingsPayload, UserRow } from "../../types";
import { isValidEmail } from "../../lib/validate";
import { ADMIN_TOOLTIP, ConfirmDialog, RoleNotice } from "./shared";

export type UsersProps = {
  settings: SettingsPayload;
  role: Role | null;
  onInvalidate: () => void;
};

const ROLE_OPTIONS: Role[] = ["viewer", "operator", "admin"];

/** Users & roles: an inline role select per member, an invite row, and a
 *  Remove button per row. The hub 422s an attempt to remove a tenant's last
 *  admin membership — that has to land as an inline error on the row it was
 *  tried on, never a toast that scrolls away and leaves no trace. */
export function Users({ settings, role, onInvalidate }: UsersProps) {
  const toast = useToast();
  const canWrite = atLeast(role, "admin");

  const [inviteUsername, setInviteUsername] = useState("");
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<Role>("viewer");
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [removeTarget, setRemoveTarget] = useState<string | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);

  const roleMutation = useMutation({
    mutationFn: (row: UserRow & { role: string }) =>
      saveSettingsSection("users", {
        username: row.username,
        email: row.email,
        display_name: row.display_name,
        role: row.role,
      }),
    onSuccess: onInvalidate,
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  const inviteMutation = useMutation({
    mutationFn: () =>
      saveSettingsSection("users", {
        username: inviteUsername.trim(),
        email: inviteEmail.trim(),
        display_name: inviteUsername.trim(),
        role: inviteRole,
      }),
    onSuccess: () => {
      setInviteUsername("");
      setInviteEmail("");
      setInviteRole("viewer");
      setInviteError(null);
      onInvalidate();
      toast.show("Member invited.", "good");
    },
    onError: (err: Error) => setInviteError(err.message),
  });

  const removeMutation = useMutation({
    mutationFn: (username: string) => removeUser(username),
    onSuccess: () => {
      setRemoveTarget(null);
      setRemoveError(null);
      onInvalidate();
      toast.show("Member removed.", "good");
    },
    onError: (err: Error) => {
      // The last-admin guard's 422 is an inline error on the confirmation,
      // not a toast — it needs to stay visible until the admin acts on it.
      setRemoveError(err.message);
    },
  });

  function onInviteSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!inviteUsername.trim() || !inviteEmail.trim()) {
      setInviteError("Username and e-mail are both required.");
      return;
    }
    if (!isValidEmail(inviteEmail)) {
      setInviteError("Enter a valid e-mail address.");
      return;
    }
    inviteMutation.mutate();
  }

  const columns: Column<UserRow>[] = [
    { key: "display_name", header: "Name", render: (r) => r.display_name || r.username },
    { key: "email", header: "E-mail" },
    {
      key: "role",
      header: "Role",
      render: (r) => (
        <select
          aria-label={`Role for ${r.username}`}
          value={r.role}
          disabled={!canWrite}
          title={canWrite ? undefined : ADMIN_TOOLTIP}
          onChange={(e) => roleMutation.mutate({ ...r, role: e.target.value })}
        >
          {ROLE_OPTIONS.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      ),
    },
    {
      key: "remove",
      header: "",
      align: "right",
      render: (r) => (
        <button
          type="button"
          className="g-danger"
          disabled={!canWrite}
          title={canWrite ? undefined : ADMIN_TOOLTIP}
          onClick={() => {
            setRemoveError(null);
            setRemoveTarget(r.username);
          }}
        >
          Remove
        </button>
      ),
    },
  ];

  return (
    <div className="g-settings-users">
      {!canWrite && <RoleNotice role={role} required="admin" />}

      <Table columns={columns} rows={settings.users} rowKey={(r) => r.username} empty="No members yet." />

      <form className="g-settings-invite" onSubmit={onInviteSubmit} noValidate>
        <h3>Invite a member</h3>
        <label htmlFor="settings-invite-username">Username</label>
        <input
          id="settings-invite-username"
          value={inviteUsername}
          disabled={!canWrite}
          title={canWrite ? undefined : ADMIN_TOOLTIP}
          onChange={(e) => setInviteUsername(e.target.value)}
        />
        <label htmlFor="settings-invite-email">E-mail</label>
        <input
          id="settings-invite-email"
          type="email"
          value={inviteEmail}
          disabled={!canWrite}
          title={canWrite ? undefined : ADMIN_TOOLTIP}
          onChange={(e) => setInviteEmail(e.target.value)}
        />
        <label htmlFor="settings-invite-role">Role</label>
        <select
          id="settings-invite-role"
          value={inviteRole}
          disabled={!canWrite}
          title={canWrite ? undefined : ADMIN_TOOLTIP}
          onChange={(e) => setInviteRole(e.target.value as Role)}
        >
          {ROLE_OPTIONS.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
        {inviteError && <p className="g-field-error">{inviteError}</p>}
        <button type="submit" disabled={!canWrite || inviteMutation.isPending} title={canWrite ? undefined : ADMIN_TOOLTIP}>
          Invite
        </button>
      </form>

      <ConfirmDialog
        open={removeTarget !== null}
        title="Remove this member?"
        body={
          <>
            {`They will lose access to this tenant immediately.`}
            {removeError && <span className="g-field-error g-confirm-error">{removeError}</span>}
          </>
        }
        confirmLabel="Remove"
        onCancel={() => {
          setRemoveTarget(null);
          setRemoveError(null);
        }}
        onConfirm={() => {
          if (removeTarget) removeMutation.mutate(removeTarget);
        }}
      />
    </div>
  );
}

export default Users;
