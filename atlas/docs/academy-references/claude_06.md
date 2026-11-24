# Study note: claude_06

Topic searched: "claude custom connector remote mcp server oauth" (2026-10-18). Written in our own words; no transcript is kept in the repo.

## Videos studied

- https://www.youtube.com/watch?v=XEMZniYKuaY -- Shaw Talebi, 13,771 views, 21:39, read 2026-10-18. 182 words/min; first on-screen action at 358 s (transcript heuristic); 9 chapters.
- https://www.youtube.com/watch?v=IO464uc3QKs -- ChatMaxima, 149 views, 4:30, read 2026-10-18. 156 words/min; first on-screen action at 24 s (transcript heuristic); 11 chapters.

## How they open

Only two videos were usable, and neither is a clean match. Shaw Talebi's is
a ChatGPT tutorial first: Claude appears only in a short bonus at the end,
though the server and sign-in setup carry over unchanged. ChatMaxima's is a
vendor demo that connects its own hosted server; it builds nothing and
explains nothing about OAuth, so it counts only as a click-path reference.

Talebi opens on a plain promise (build a custom server, connect it to a
chat app), then recaps what MCP is before any code. ChatMaxima's narrator
names the product and the outcome in one breath, with background music,
and goes straight into signing in to Claude. Neither shows the finished
tool call first.

## The order they explain things

- Talebi: MCP as client and server, why a server on the internet needs
  sign-in, the three parts (server, client, authorization server), the full
  handshake on a slide (resource metadata, server metadata, optional
  dynamic client registration, browser sign-in, code, token), then four
  steps: write the server with the Python SDK, deploy to a hosting service,
  configure the identity provider, add the connector; Claude last.
- ChatMaxima: sign in, Customize, Connectors, add custom connector, paste
  the URL, the consent screen, pick a workspace, allow, then a first prompt
  that builds a chatbot, the per-tool permission prompts, and a tour of the
  result in the vendor's own builder.

## Examples and analogies

- A server with one tool that fetches a video's transcript from a link,
  shown failing without the tool and working with it (Talebi).
- A dental appointment booking bot built from one prompt (ChatMaxima).
- MCP framed as an open standard, so a server built for one chat app works
  in another unchanged (Talebi). No real analogy in either video.

## Beginner mistakes they address

- Binding the server to the loopback address on the host, so nothing
  outside can reach it (Talebi).
- Leaving out scopes: one client refused to connect until every scope was
  listed (Talebi).
- An audience versus resource mismatch between client and identity
  provider, fixed with a default audience setting he found on a forum.
- Forgetting to enable dynamic client registration, or leaving sign-ups
  open so strangers can use the server (Talebi).
- Opening the bare host URL instead of the path ending in the server route;
  an unauthenticated visit returning an auth-required error is expected.

## Pacing

Measured above: about 156 and 182 words a minute. Talebi is steady but
long, and his first real action comes six minutes in after the concept and
handshake slides. ChatMaxima reaches a click at 24 s and then narrates
every click in the same upbeat sentence shape, so nothing stands out.
Talebi names chapters by step; ChatMaxima has eleven chapters for four and
a half minutes, one per screen.

## What they do badly, and what we do better

- Talebi's lesson is about ChatGPT, and much of it is one identity
  provider's settings screens. We stay on Claude and on what any server
  must do (answer 401, publish its metadata), not one vendor's dashboard.
- The handshake slide arrives before the viewer knows why it matters, with
  a warning that it gets complicated. We show the sign-in from the user's
  side first, then explain each hop it just made.
- Talebi admits days of trial and error and leans on a forum workaround. We
  state only what the docs say and mark anything provider-specific.
- ChatMaxima is a product ad; the safety warning about unverified servers
  and reviewing scopes is never said. We say it every time a consent screen
  is on screen.
- Neither shows how Claude Code adds the same server, or how to revoke it.

## What we take (structure only)

1. Open on the connector working: a question answered by a custom tool.
2. Draw the three parts (Claude, the server, the sign-in service) before
   any detail, and keep the drawing on screen while explaining.
3. Walk the handshake in the order the user sees it, one hop per beat.
4. Show the add-connector path in Claude, then the Claude Code command.
5. Close on the trust warning and how to remove access.
