// Small, framework-free validation/formatting helpers shared by more than
// one Settings form. Kept out of the `.tsx` files that use them so the
// react-refresh lint rule (which flags a component file that also exports
// a plain function) stays clean.

/** The one e-mail shape check every Settings form needs (Profile's owner
 *  e-mail, the Users invite row). Kept in one place so the two forms can't
 *  quietly drift apart on what counts as "valid". */
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function isValidEmail(value: string): boolean {
  return EMAIL_RE.test(value.trim());
}

/** Derives a tenant slug from its name the same way the hub does: lowercase,
 *  any run of non-alphanumerics collapsed to one `-`, no leading or
 *  trailing dash, capped at 24 characters. */
export function slugify(name: string): string {
  const base = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return base.slice(0, 24).replace(/-+$/g, "");
}
