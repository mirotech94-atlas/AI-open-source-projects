import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Table, useToast } from "../../design";
import type { Column } from "../../design";
import { createTenant, deleteTenant, updateTenant } from "../../lib/api";
import { slugify } from "../../lib/validate";
import type { SettingsPayload, TenantRow } from "../../types";
import { ConfirmDialog } from "./shared";

export type TenantsProps = {
  settings: SettingsPayload;
  onInvalidate: () => void;
};

// The brand accent is a picker over a fixed set of blues (the only hue the
// rest of the design system uses — see `tokens.css`'s own comment: "Blue +
// white base, ... no other hues"), not a free colour input.
const ACCENT_SWATCHES: { value: string; label: string }[] = [
  { value: "#1c5cff", label: "Primary blue" },
  { value: "#0c3fc9", label: "Deep blue" },
  { value: "#5b8dff", label: "Sky blue" },
  { value: "#0b3b8f", label: "Navy" },
];

function brandField(brand: Record<string, unknown>, key: string): string {
  const v = brand[key];
  return typeof v === "string" ? v : "";
}

/** Tenants — superadmin only. List/create/rename, the brand (name, accent,
 *  domain) and ntfy topic per tenant, and a demo toggle that reveals the
 *  fixed sign-in code demo tenants use in place of OTP. Not role-gated the
 *  way the other sections are: this whole page only renders for
 *  `me.user.is_superadmin`, so every control here is always live. */
export function Tenants({ settings, onInvalidate }: TenantsProps) {
  const toast = useToast();
  const [newName, setNewName] = useState("");
  const [newSlug, setNewSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [editing, setEditing] = useState<Record<string, string>>({});
  const [brandDrafts, setBrandDrafts] = useState<Record<string, { name?: string; accent?: string; domain?: string }>>({});
  const [deleteTarget, setDeleteTarget] = useState<TenantRow | null>(null);

  const createMutation = useMutation({
    mutationFn: (body: { name: string; slug: string }) => createTenant(body),
    onSuccess: () => {
      setNewName("");
      setNewSlug("");
      setSlugTouched(false);
      onInvalidate();
      toast.show("Tenant created.", "good");
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  const renameMutation = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => updateTenant(id, { name }),
    onSuccess: onInvalidate,
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  const ntfyMutation = useMutation({
    mutationFn: ({ id, ntfy_topic }: { id: string; ntfy_topic: string }) => updateTenant(id, { ntfy_topic }),
    onSuccess: onInvalidate,
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  const brandMutation = useMutation({
    mutationFn: ({ id, brand }: { id: string; brand: { name?: string; accent?: string; domain?: string } }) =>
      updateTenant(id, { brand }),
    onSuccess: (_data, { id }) => {
      setBrandDrafts((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
      onInvalidate();
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  const demoMutation = useMutation({
    mutationFn: ({ id, demo }: { id: string; demo: boolean }) => updateTenant(id, { demo }),
    onSuccess: onInvalidate,
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  function draftBrand(r: TenantRow) {
    return {
      name: brandDrafts[r.id]?.name ?? brandField(r.brand, "name"),
      accent: brandDrafts[r.id]?.accent ?? brandField(r.brand, "accent") ?? ACCENT_SWATCHES[0].value,
      domain: brandDrafts[r.id]?.domain ?? brandField(r.brand, "domain"),
    };
  }

  function setBrandDraft(id: string, patch: Partial<{ name: string; accent: string; domain: string }>) {
    setBrandDrafts((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }));
  }

  function saveBrand(r: TenantRow) {
    const draft = draftBrand(r);
    brandMutation.mutate({ id: r.id, brand: draft });
  }

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteTenant(id),
    onSuccess: () => {
      setDeleteTarget(null);
      onInvalidate();
      toast.show("Tenant deleted.", "good");
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  const columns: Column<TenantRow>[] = [
    {
      key: "name",
      header: "Name",
      render: (r) => (
        <input
          aria-label={`Name for ${r.slug}`}
          value={editing[r.id] ?? r.name}
          onChange={(e) => setEditing((prev) => ({ ...prev, [r.id]: e.target.value }))}
          onBlur={() => {
            const value = editing[r.id];
            if (value !== undefined && value !== r.name) renameMutation.mutate({ id: r.id, name: value });
          }}
        />
      ),
    },
    { key: "users", header: "Users" },
    { key: "sensors", header: "Sensors" },
    {
      key: "brand_name",
      header: "Brand name",
      render: (r) => (
        <input
          aria-label={`Brand name for ${r.slug}`}
          value={draftBrand(r).name}
          onChange={(e) => setBrandDraft(r.id, { name: e.target.value })}
        />
      ),
    },
    {
      key: "brand_accent",
      header: "Accent",
      render: (r) => (
        <select
          aria-label={`Accent for ${r.slug}`}
          value={draftBrand(r).accent}
          onChange={(e) => setBrandDraft(r.id, { accent: e.target.value })}
        >
          {ACCENT_SWATCHES.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
      ),
    },
    {
      key: "brand_domain",
      header: "Domain",
      render: (r) => (
        <input
          aria-label={`Domain for ${r.slug}`}
          value={draftBrand(r).domain}
          onChange={(e) => setBrandDraft(r.id, { domain: e.target.value })}
          placeholder="reports.example.com"
        />
      ),
    },
    {
      key: "brand_save",
      header: "",
      render: (r) =>
        brandDrafts[r.id] ? (
          <button type="button" onClick={() => saveBrand(r)} disabled={brandMutation.isPending}>
            Save brand
          </button>
        ) : null,
    },
    {
      key: "ntfy_topic",
      header: "ntfy topic",
      render: (r) => (
        <input
          aria-label={`ntfy topic for ${r.slug}`}
          defaultValue={r.ntfy_topic}
          onBlur={(e) => {
            if (e.target.value !== r.ntfy_topic) ntfyMutation.mutate({ id: r.id, ntfy_topic: e.target.value });
          }}
        />
      ),
    },
    {
      key: "demo",
      header: "Demo",
      render: (r) => (
        <label className="g-settings-demo-toggle">
          <input
            type="checkbox"
            aria-label={`Demo mode for ${r.slug}`}
            checked={r.demo}
            onChange={(e) => demoMutation.mutate({ id: r.id, demo: e.target.checked })}
          />
          {r.demo && (
            <span className="mono">
              sign-in code: {typeof r.brand.demo_code === "string" ? r.brand.demo_code : "424242"}
            </span>
          )}
        </label>
      ),
    },
    {
      key: "delete",
      header: "",
      align: "right",
      render: (r) => (
        <button type="button" className="g-danger" onClick={() => setDeleteTarget(r)}>
          Delete
        </button>
      ),
    },
  ];

  return (
    <div className="g-settings-tenants">
      <Table columns={columns} rows={settings.tenants} rowKey={(r) => r.id} empty="No tenants yet." />

      <form
        className="g-settings-new-tenant"
        onSubmit={(e) => {
          e.preventDefault();
          const name = newName.trim();
          const slug = newSlug.trim() || slugify(name);
          if (name && slug) createMutation.mutate({ name, slug });
        }}
      >
        <label htmlFor="settings-new-tenant-name">New tenant name</label>
        <input
          id="settings-new-tenant-name"
          value={newName}
          onChange={(e) => {
            const value = e.target.value;
            setNewName(value);
            if (!slugTouched) setNewSlug(slugify(value));
          }}
        />
        <label htmlFor="settings-new-tenant-slug">Slug</label>
        <input
          id="settings-new-tenant-slug"
          value={newSlug}
          onChange={(e) => {
            setSlugTouched(true);
            setNewSlug(slugify(e.target.value));
          }}
          maxLength={24}
        />
        <button type="submit" disabled={createMutation.isPending}>
          Create tenant
        </button>
      </form>

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete this tenant?"
        body={`${deleteTarget?.name ?? "This tenant"} and everything it watches will be removed permanently.`}
        confirmLabel="Delete"
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => {
          if (deleteTarget) deleteMutation.mutate(deleteTarget.id);
        }}
      />
    </div>
  );
}

export default Tenants;
