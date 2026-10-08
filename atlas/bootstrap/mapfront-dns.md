# mapfront.io: DNS at Porkbun, and turning the hosting on

Mapfront is Miro's web agency. `https://mapfront.io` serves the landing page
and `https://<slug>.mapfront.io` serves each client site that
`agency.deploy_site` (Tier 2) has published. All of it is served by Caddy on
the ATLAS server, **203.0.113.10**, whose certificates come from Let's
Encrypt on demand and only after the api's `/internal/tls-ask` says yes
(`atlas/api/tls_ask.py`).

## 1. DNS records (Porkbun → mapfront.io → DNS)

First **delete Porkbun's default parking records** on `@` and `*` (an ALIAS
or CNAME to `pixie.porkbun.com`, or A records to Porkbun's parking address).
They conflict with the records below, and while they are there Let's Encrypt
reaches Porkbun instead of this server.

| Type | Host | Answer | TTL |
|---|---|---|---|
| A | *(blank / `@`)* | `203.0.113.10` | 600 |
| A | `*` | `203.0.113.10` | 600 |
| TXT | `_dmarc` | `v=DMARC1; p=none; rua=mailto:miro@mapfront.io` | 600 |

No AAAA records: the server is not set up for IPv6 here.

**Email (miro@mapfront.io) comes from Porkbun Email.** Turn it on for the
domain in Porkbun, and let Porkbun add its own records: MX, the SPF TXT on
`@`, and DKIM. Do not type those by hand; use whatever Porkbun's email setup
page shows. The `*` A record does not interfere with mail: MX records are
looked up by name, and a wildcard A does not answer an MX query.

If Porkbun's email setup asks for a record on a host label (for example
`mail`, `autodiscover`, `autoconfig`, `_domainkey`), that label must stay
out of the client-site namespace. Those labels are already reserved in
`atlas/modules/agency/hosting.py::RESERVED`. Add any new label Porkbun asks
for to that list; the deploy then refuses it as a slug.

Start with DMARC at `p=none` (reports only). Once the reports show that SPF
and DKIM pass for everything sent as mapfront.io, move to `p=quarantine`.

Check before deploying:

    dig +short mapfront.io A                 # 203.0.113.10
    dig +short anything.mapfront.io A        # 203.0.113.10
    dig +short mapfront.io MX                # Porkbun's mail hosts
    dig +short _dmarc.mapfront.io TXT

Add DNS before the deploy that brings in the Caddyfile. Caddy requests
nothing until a real TLS handshake arrives (every Mapfront block is
`on_demand`). If a visitor arrives before DNS has propagated, the HTTP-01
challenge fails, and Let's Encrypt allows only 5 failures per name per hour.

## 2. Server preparation (once, before the deploy)

    # The api (uid 10001) writes published sites here and Caddy mounts it
    # read-only. If it does not exist when caddy is recreated, Docker creates
    # it as root and every deploy fails with "Permission denied".
    install -d -o 10001 -g 10001 /opt/atlas/data/app/sites

    # .env on the server. set-secret.sh or setenv.py, as usual:
    #   AGENCY_DEPLOY_HOST=local
    #   AGENCY_SITES_DOMAIN=mapfront.io
    #   AGENCY_POSTAL_ADDRESS=<the real postal address, when there is one>

Also check that `MESH_ALLOWED_CIDRS` in the server's `.env` is unset or still
includes `172.16.0.0/12`. Caddy's TLS ask arrives from the Docker bridge; if
that range is missing, every ask is refused and no certificate is ever issued.

## 3. Deploy

Merge to `phases-12-14` and let GitLab CD run `deploy.sh <sha>`. The
Caddyfile changes, so deploy.sh validates it, recreates the api, and then
recreates caddy, which picks up the two new read-only mounts from
docker-compose.yml.

After changing `AGENCY_POSTAL_ADDRESS`, recreate caddy so the landing page
sees the new value (`cd /opt/atlas && docker compose up -d --force-recreate
--no-deps caddy`, the same command deploy.sh runs). The api reads it for
outreach at send time.

## 4. Verify

    curl -sI https://mapfront.io | head -1                   # 200
    curl -sI https://www.mapfront.io | grep -i location       # https://mapfront.io/
    curl -s  https://mapfront.io | grep -c '<address>'        # 1 once the address is set
    curl -sI https://nope.mapfront.io                         # TLS failure: ask said no

Then deploy a client site: approve the `agency.deploy_site` card for a built
slug. The executor reads it back at `https://<slug>.mapfront.io/`, and that
URL should serve the site's title.

## What is exposed

Only static files: the landing page, and the published release of each
deployed slug. No path under `mapfront.io` is proxied to the api. Dotfiles
and the `.releases` store are 404. There is no directory listing. The
`atlas.example.com` block is unchanged.
