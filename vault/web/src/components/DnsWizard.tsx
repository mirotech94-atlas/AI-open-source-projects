import { useState } from "react";
import { confirmRouterDns, setRouterDns } from "../lib/api";
import type { DnsWizard as DnsWizardPayload } from "../types";

export type DnsWizardProps = {
  wizard: DnsWizardPayload;
};

/** Three-step setup wizard: point the router's DNS at the resolver, reboot
 *  or renew a lease on one device, then a live check that goes green once
 *  the hub reports traffic actually flowing through it. The page re-fetches
 *  the DNS payload every 10s (see `Dns.tsx`), so this component only ever
 *  reads whatever `wizard` it was just handed — it does not poll itself. */
export function DnsWizard({ wizard }: DnsWizardProps) {
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [pending, setPending] = useState<{ token: string; summary: string } | null>(null);
  const working = wizard.working && wizard.clients_using > 0;

  /** Step one of two: ask the hub for the summary and the one-shot token.
   *  Nothing is written yet — a person still has to read it and say yes. */
  async function setItForMe() {
    setBusy(true); setNote("");
    try {
      const out = await confirmRouterDns(wizard.resolver_ip);
      setPending({ token: out.token, summary: out.summary });
    } catch (e) {
      setNote(e instanceof Error ? e.message : "The router could not be asked.");
    } finally {
      setBusy(false);
    }
  }

  async function confirmIt() {
    if (!pending) return;
    setBusy(true);
    try {
      await setRouterDns(wizard.resolver_ip, pending.token);
      setPending(null);
      setNote("Queued. The sensor will apply it within a few seconds — then renew a "
              + "device's lease so it picks the change up.");
    } catch (e) {
      setNote(e instanceof Error ? e.message : "That did not go through.");
    } finally {
      setBusy(false);
    }
  }

  async function copyResolver() {
    try {
      await navigator.clipboard.writeText(wizard.resolver_ip);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard access can be denied (no permission, non-secure context) —
      // the resolver IP is still shown as plain text either way.
    }
  }

  return (
    <div className="g-dns-wizard">
      <ol className="g-dns-wizard-steps">
        <li className="g-dns-wizard-step">
          <p>
            Point your router's DNS at <strong className="mono">{wizard.resolver_ip || "—"}</strong>
          </p>
          {/* The question this step actually raises, answered where it is
              asked: "am I handing my browsing to something I trust?" You
              are not replacing Cloudflare with Vault -- Vault drops
              the known-malicious names and Cloudflare resolves the rest,
              over an encrypted connection your ISP cannot read. Setting the
              router to 1.1.1.1 directly would skip the filtering, which is
              the whole product. */}
          <p className="g-dns-wizard-trust">
            Two layers, not one. Vault checks each name against its own
            phishing and malware lists — blocking it, logging it, and showing
            it in the live query stream where your allow and block rules
            apply — then forwards everything else to{" "}
            <strong className="mono">Cloudflare 1.1.1.2 / 1.0.0.2</strong>, which
            blocks known malware and phishing again on its own threat
            intelligence, over encrypted DNS your provider cannot read.
            Quad9, which also filters malware, is the fallback.
          </p>
          <p className="g-dns-wizard-hint">
            Cloudflare&rsquo;s plain <span className="mono">1.1.1.1</span> resolves
            everything and filters nothing, so Vault never uses it. If you
            would rather skip Vault entirely, point the router at{" "}
            <span className="mono">1.1.1.2</span> — you keep malware filtering,
            and lose the logging, the per-device view and your own rules.
          </p>
          <button type="button" onClick={copyResolver} disabled={!wizard.resolver_ip}>
            {copied ? "Copied" : "Copy"}
          </button>
          {/* The LAN gateway: the box you go and change the setting ON, as
              opposed to the resolver address above, which is what you set it
              TO. Rendered bare it was two addresses stacked with nothing
              saying which was which. Mono for the same reason the line above
              is mono -- it is an address, and the reader is about to type
              it. */}
          {wizard.router_hint && (
            <p className="g-dns-wizard-hint">
              Your router is at <span className="mono">{wizard.router_hint}</span>
            </p>
          )}
          {/* Item 38: the setting is never called the same thing twice, and
              "point your router's DNS at this" assumes the reader already
              knows where that lives. These are the names it actually goes
              by on the boxes people have. */}
          {/* Miro's call: Vault can do this itself. The manual
              instructions stay underneath, because a router Vault has no
              credential for — or a vendor it does not speak — still has to
              be walkable by hand. */}
          <div className="g-dns-wizard-auto">
            <button type="button" className="g-btn-primary" onClick={() => void setItForMe()}
                    disabled={busy || !wizard.resolver_ip}>
              {busy ? "Asking the router…" : "Let Vault set it for me"}
            </button>
            <span className="hint">
              Vault signs in to the router and writes the DNS field only. It needs the
              router password saved in Config first, and a Test that has passed.
            </span>
          </div>
          {note && <p className="g-dns-wizard-note" role="status">{note}</p>}
          {pending && (
            <div className="g-dns-wizard-confirm" role="alertdialog" aria-label="Confirm the DNS change">
              <p>{pending.summary}</p>
              <div className="g-dns-wizard-confirm-foot">
                <button type="button" className="g-btn-ghost" onClick={() => setPending(null)}>Cancel</button>
                <button type="button" className="g-btn-primary" onClick={() => void confirmIt()}>
                  Change it
                </button>
              </div>
            </div>
          )}

          <details className="g-dns-wizard-help">
            <summary>Where is that setting? (or do it by hand)</summary>
            <p>
              Open <span className="mono">{wizard.router_hint || "your router"}</span> in a browser and
              sign in. The setting is usually under <b>Internet</b>, <b>WAN</b> or <b>LAN</b>, and is
              called <b>DNS</b>, <b>DNS Server</b>, <b>Name Server</b> or <b>Custom DNS</b>. Switch it
              from <i>Automatic</i> or <i>From ISP</i> to <i>Manual</i>, put the address above in the
              first box, and leave the second blank — a second entry is a way around Vault, and
              devices will take it whenever it answers first.
            </p>
            <p>
              If your router will not accept a single DNS server, put the address above in both boxes
              rather than adding someone else&rsquo;s.
            </p>
          </details>
        </li>
        <li className="g-dns-wizard-step">
          <p>Make one device pick up the change.</p>
          {/* Devices cache the DNS server they were handed at join time, so
              nothing moves until a lease is renewed -- which is why this
              step exists at all and why "I changed it and nothing happened"
              is the usual next message. */}
          <p className="g-dns-wizard-hint">
            Devices keep the DNS server they were given when they joined, so the change does not
            reach them until their lease renews. Turning Wi-Fi off and on again is enough:
          </p>
          <ul className="g-dns-wizard-ways">
            <li><b>macOS</b> — Wi-Fi menu, turn Wi-Fi off, then on.</li>
            <li><b>iPhone / iPad</b> — Settings, Wi-Fi, the <b>i</b> beside your network, Renew Lease.</li>
            <li><b>Windows</b> — disconnect and reconnect the network, or run <span className="mono">ipconfig /renew</span>.</li>
            <li><b>Android</b> — forget the network and join it again.</li>
          </ul>
        </li>
        <li className="g-dns-wizard-step">
          <p
            data-testid="wizard-check"
            role="status"
            className={`g-dns-wizard-check g-tone-${working ? "good" : "plain"}`}
          >
            {working
              ? `Working — ${wizard.clients_using} client${wizard.clients_using === 1 ? "" : "s"} using Vault DNS.`
              : "Not seeing traffic through Vault DNS yet."}
          </p>
          {!working && (
            <details className="g-dns-wizard-help">
              <summary>It has been a few minutes and nothing has changed</summary>
              <ul className="g-dns-wizard-ways">
                <li>
                  The router saved the setting but did not apply it. Many need a reboot before a DNS
                  change takes effect — check the setting is still there after it comes back.
                </li>
                <li>
                  A second DNS server is still listed. Devices use whichever answers first, so a
                  backup entry means most lookups never reach Vault.
                </li>
                <li>
                  The device is using DNS-over-HTTPS of its own. Firefox and Chrome can resolve names
                  themselves, bypassing whatever the router says — turn off &ldquo;Secure DNS&rdquo; in
                  the browser&rsquo;s privacy settings to test.
                </li>
                <li>
                  The device has a DNS server set by hand from an earlier experiment. That always
                  beats what the router hands out.
                </li>
              </ul>
            </details>
          )}
        </li>
      </ol>
    </div>
  );
}

export default DnsWizard;
