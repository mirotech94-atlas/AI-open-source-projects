import { TimezoneSettings } from "../../components/TimezoneSettings";
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useToast } from "../../design";
import { saveSettingsSection } from "../../lib/api";
import { atLeast } from "../../lib/auth";
import type { Role } from "../../lib/auth";
import type { SettingsPayload } from "../../types";
import { isValidEmail } from "../../lib/validate";
import { ADMIN_TOOLTIP, RoleNotice } from "./shared";

export type ProfileProps = {
  settings: SettingsPayload;
  role: Role | null;
  onInvalidate: () => void;
};

type FieldErrors = Partial<Record<"owner" | "email" | "address", string>>;

function validate(owner: string, email: string): FieldErrors {
  const errors: FieldErrors = {};
  if (!owner.trim()) errors.owner = "Owner name is required.";
  if (email.trim() && !isValidEmail(email)) {
    errors.email = "Enter a valid e-mail address.";
  }
  return errors;
}

/** Profile: the owner/company/e-mail/address that print on a report cover,
 *  with a live preview line so the admin sees exactly how the cover reads
 *  before saving — no round trip needed to check formatting. */
export function Profile({ settings, role, onInvalidate }: ProfileProps) {
  const toast = useToast();
  const canWrite = atLeast(role, "admin");

  const [owner, setOwner] = useState(settings.profile.owner);
  const [company, setCompany] = useState(settings.profile.company);
  const [email, setEmail] = useState(settings.profile.email);
  const [address, setAddress] = useState(settings.profile.address);
  const [errors, setErrors] = useState<FieldErrors>({});

  const mutation = useMutation({
    mutationFn: () => saveSettingsSection("profile", { owner, company, email, address }),
    onSuccess: () => {
      onInvalidate();
      toast.show("Profile saved.", "good");
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const next = validate(owner, email);
    setErrors(next);
    if (Object.keys(next).length === 0) mutation.mutate();
  }

  const previewLine = [owner || "—", company].filter(Boolean).join(" · ");

  return (
    <>
    <form className="g-settings-form" onSubmit={onSubmit} noValidate>
      {!canWrite && <RoleNotice role={role} required="admin" />}

      <label htmlFor="settings-profile-owner">Owner</label>
      <input
        id="settings-profile-owner"
        value={owner}
        disabled={!canWrite}
        title={canWrite ? undefined : ADMIN_TOOLTIP}
        onChange={(e) => setOwner(e.target.value)}
        aria-invalid={errors.owner ? true : undefined}
      />
      {errors.owner && <p className="g-field-error">{errors.owner}</p>}

      <label htmlFor="settings-profile-company">Company</label>
      <input
        id="settings-profile-company"
        value={company}
        disabled={!canWrite}
        title={canWrite ? undefined : ADMIN_TOOLTIP}
        onChange={(e) => setCompany(e.target.value)}
      />

      <label htmlFor="settings-profile-email">E-mail</label>
      <input
        id="settings-profile-email"
        type="email"
        value={email}
        disabled={!canWrite}
        title={canWrite ? undefined : ADMIN_TOOLTIP}
        onChange={(e) => setEmail(e.target.value)}
        aria-invalid={errors.email ? true : undefined}
      />
      {errors.email && <p className="g-field-error">{errors.email}</p>}

      <label htmlFor="settings-profile-address">Address</label>
      <input
        id="settings-profile-address"
        value={address}
        disabled={!canWrite}
        title={canWrite ? undefined : ADMIN_TOOLTIP}
        onChange={(e) => setAddress(e.target.value)}
      />

      <div className="g-settings-preview">
        <span className="g-settings-preview-label">Report cover preview</span>
        <p className="g-settings-preview-line">{previewLine}</p>
      </div>

      <button type="submit" disabled={!canWrite || mutation.isPending} title={canWrite ? undefined : ADMIN_TOOLTIP}>
        Save
      </button>
    </form>
    {/* Outside the profile form on purpose: it saves through its own
        endpoints, and inside the form Enter in its timezone field submitted
        the profile instead. It once sat inside the report-cover preview box
        too, drawn as if it were part of the cover. */}
    <TimezoneSettings />
    </>
  );
}

export default Profile;
