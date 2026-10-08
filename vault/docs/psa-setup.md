# Connecting a customer to the MSP's PSA

Guardian files one ticket per escalated finding on HaloPSA, Autotask or
ConnectWise Manage, notes the fix on it, optionally books time, resolves
it when the finding resolves, and acks the finding here when a technician
closes the ticket there. This is the ten-minute setup for the first live
customer, and what to do when a vendor's API does not match its
documentation. (`hub/guardian_hub/integrations/psa.py` is the code; the
request shapes there follow the vendors' published REST docs and have not
yet been exercised against a live tenant of any of the three.)

## What you type, in the customer's Integrations page

Open the customer's tenant, Settings, Integrations, and pick the PSA card.
Type **names as they appear in the PSA**, not ids. Guardian resolves each
name to the PSA's own id when you press Test, tells you what it found, and
keeps the answer.

| Field | HaloPSA | Autotask | ConnectWise Manage |
|---|---|---|---|
| Base URL | `https://<yours>.halopsa.com` | your zone's API host, e.g. `https://webservices2.autotask.net` | `https://<yours>.connectwise.com` or your on-prem host |
| Secret | the API application's **client secret** | the API user's **secret** | the API member's **private key** |
| Client id | the API application's **client id** | the **integration code** (ApiIntegrationCode) | the **clientId** issued at developer.connectwise.com |
| API user | (unused) | the API user's **username** | `<company id>+<public key>` |
| Company | the client's name | the company name | the company name |
| Board | ticket type name (optional) | queue name (optional) | board name (recommended) |
| Closed status | blank = Halo's stock closed status (id 9) | blank = "Complete" (5) | blank = a status named "Closed"; ConnectWise also honours any status whose `closedFlag` is set |
| Technician id | (unused) | the **resource id** time is booked to; Autotask refuses a time entry without one | (unused) |

"File a ticket for" is CRIT by default; "CRIT+WARN" widens it. "Book time
for fixes" is **off** by default and stays off unless you turn it on for
that customer.

## What the PSA-side user needs

- **HaloPSA**: an API application with client-credentials login, permission
  to read Clients, Ticket Types and Statuses, and to create Tickets and
  Actions.
- **Autotask**: an API user (security level "API User (system)") with
  access to Companies, Tickets, Ticket Notes, Time Entries. The integration
  code comes from the vendor's integration record.
- **ConnectWise Manage**: an API member with read on Companies and Service
  Boards, and create/update on Service Tickets, Notes and Time Entries.
  ClientId is per integration, issued by ConnectWise.

## Press Test, read what it says

Test does not file a ticket. It connects, resolves every name you typed,
and reports:

> Connected to ConnectWise. Company 'Northwind Dental' is id 42. Board
> 'Guardian' is id 7. Resolved tickets move to status Done (509).

A name that matches nothing or more than one thing fails the test and
lists the nearest names it did find. Copy one of them into the field and
Test again. A permissions problem reads as "the PSA returned no companies
at all"; fix the API user's rights first.

## When a vendor's real API differs from its docs

The card shows the first thing that went wrong, as the call, its status,
and the first line of the reply, for example:

> sec:attacker:demo:1.2.3.4: POST /service/tickets -> 400: {"code":"InvalidObject","message":"board is required"}

or, when the reply came back 200 but Guardian could not find the ticket id
in it:

> POST /Tickets -> 200: created nothing I can read an id from; reply keys: item, ticketNumber

That line names the seam to adjust. Each vendor's request and response
shape lives in exactly one function in `psa.py`:

| What failed | Function | What to change |
|---|---|---|
| ticket not created / id not found | `_create` | the payload keys, or which reply key holds the id |
| note missing on the ticket | `_note` | the note endpoint or its field names |
| time not booked | `_time_entry` | the time-entry payload |
| ticket not closing | `_resolve` | the status field or the closed value |
| ticket closed on the PSA but finding not acked | `_status` | how "closed" is read from the ticket |
| Test cannot resolve names | `_companies`, `_boards`, `_statuses` | the list endpoints or the name/id keys |

Change the one function, keep its signature, and the tests in
`hub/tests/test_psa.py` (the "HTTP shapes" section) are where the new
shape is pinned so it cannot drift back. Nothing else in the sync loop
knows a vendor's field names.

One rule to keep while adjusting: a reply Guardian cannot use must raise
`PsaError`, never return None. The whole point of this design is that a
ticket which never appears says why on the card.
