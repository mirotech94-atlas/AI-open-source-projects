# WhatsApp link (Miro's own number as a linked device)

ATLAS reaches Miro on +1 555 010 0142 through a linked device (WhatsApp Web style),
not the Business API. One chat, Miro's own "Message yourself". Tags separate sources:
`[Contact]` website form, `[Portal]` agency events, `[Alert]` problems.

## Ban risk, in five lines
1. Unofficial client (Baileys): WhatsApp can disconnect or ban a number that automates; the number is Miro's personal one, so the risk is his to accept (he chose it).
2. Mitigation: the bridge sends ONLY to `WHATSAPP_OWNER` (self-chat); any other number is refused in code (bridge and ATLAS side). No bulk, no cold outreach, ever.
3. Mitigation: hard cap 20 messages/hour in the bridge (env can only lower it); ATLAS shares one cap of 15/hour across contact and portal messages; identical text is sent once an hour; low-value items are digested.
4. Mitigation: typing presence plus 1-8 s random delay per send, one send at a time, quiet hours 23:00-07:00 ET except `[Alert]`.
5. If WhatsApp logs the device out the bridge does NOT retry; delivery then reports failed/UNKNOWN and falls back to Twilio only if the sandbox is joined. Pair again by wiping the `atlas_wa_auth` volume.

## Run it
`docker compose -p atlas-agency -f docker-compose.yml -f docker-compose.local.yml up -d --build --no-deps wa-bridge`
(profile `wa`, opt-in). Needs `WA_BRIDGE_SECRET` and `WHATSAPP_OWNER` in `.env`; the api
also needs `WA_BRIDGE_SECRET` and `WA_BRIDGE_URL=http://wa-bridge:8090` (restart it to read them).
Pair: open http://127.0.0.1:8091/pair, WhatsApp > Settings > Linked devices > Link a device.
The API (:8090) is on the compose network only; session credentials live in the volume
`atlas_wa_auth` and are never printed.

## Routing (contact.py `wa_route`)
Bridge connected as the owner -> bridge. Bridge unreachable and `WHATSAPP_TWILIO_JOINED=1`
with Twilio configured -> Twilio. Anything else -> no route: the row stays pending (no
attempt used) and the contact-delivery health check reads UNKNOWN, never OK.

## Inbound and the reply command (documented, not acted on)
Messages Miro writes in the chat are stored by the bridge (`/data/inbound.jsonl`, 0600,
marked untrusted, text capped) and readable at `GET /inbound?since=<seq>`. Nothing reads
them yet. Reserved format: `/reply <message id> <text>`; the bridge parses it into
`command:{type:"reply", message_id, text}` as data only. A later executor action may relay
it by email to that contact only if: the id is a real contact row, the text is wrapped as
untrusted until Miro's own explicit command, a Tier decision is assigned in code, and the
client wrote first. Until that action exists, replies go by email (Reply-To) or the phone.
