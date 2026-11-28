# Study note: local_09

Topic searched: "Hermes Agent n8n MCP" (2026-11-25). Written in our own words; no transcript is kept in the repo.

## Videos studied

- https://www.youtube.com/watch?v=s96JeuuwLzc -- "I'll never use n8n the same......", NetworkChuck, 711,962 views, 17:25, read 2026-11-25. 206 words/min; first on-screen action at 19 s (transcript heuristic); 7 chapters.
- https://www.youtube.com/watch?v=jOK10k70XWE -- "OpenClaw vs Hermes Agent (Don't choose WRONG!)", Metics Media, 301,125 views, 9:29, read 2026-11-25. 179 words/min; first on-screen action not detectable; 8 chapters.
- https://www.youtube.com/watch?v=ZEgesa07-WI -- "DeepSeek V4 Pro + Hermes Agent: Build n8n Workflows via Chat", Derek Cheung | AI Agents Automation, 228,640 views, 12:03, read 2026-11-25. 171 words/min; first on-screen action at 103 s (transcript heuristic); 7 chapters.

## How they open

NetworkChuck opens on a result: n8n running a coding agent inside a
workflow, with a promise that the viewer will see it. Honest drift: that video
is about n8n plus Claude Code over SSH, not Hermes, and Metics Media compares
two agents without touching n8n at all. Only Derek Cheung is on our topic. He
opens by showing the finished system, a support bot built by chatting with
Hermes, and says the bot is a side point next to what the agent does with the
n8n connection over time.

## The order they explain things

- Derek Cheung: demo first, then hosting Hermes from a template, a model
  account and key, connecting a chat app, then in n8n's settings the
  instance-level MCP page with a URL and an access token, telling Hermes in
  chat to connect, a restart, a simple test workflow it builds, then a
  database skill and a bigger workflow, ending with a fix by chat.
- NetworkChuck: what you need (n8n, a terminal agent), where to install it,
  the SSH node and its credentials, test with a plain command, then the agent
  in print mode, then session IDs to continue a conversation, then a chat
  interface to it from a phone.
- Metics Media: what each agent is, where each wins, install effort, running
  costs, stability and security, then who should pick which.

## Examples and analogies

- Orchestrator versus the IT department: n8n organises, the agent does the
  technical work (NetworkChuck).
- A support bot built purely by conversation, with no canvas work, as the
  proof that the connection works (Derek Cheung).
- One agent that gets sharper because it logs what it built last time
  (Derek Cheung, Metics Media).
- Self-improving versus self-proposing, as the key difference between the two
  agents (Metics Media).

## Beginner mistakes they address

- Adding a server, then restarting the agent before the new tools can be
  used (Derek does this step on screen).
- A first workflow that is almost right but has a wrong node version, fixed by
  asking for the change rather than editing by hand (Derek).
- Credentials still unset on a generated workflow before publishing (Derek).
- Command-line agents that fail only when started over SSH, which
  NetworkChuck says has several causes.
- Forgetting a conversation ID, so every call starts with no memory
  (NetworkChuck).
- Running the agent on a daily laptop instead of a separate server (Metics).

## Pacing

Derek Cheung is the slowest at about 171 words a minute and the closest to a
step-by-step guide, though some steps are skipped over quickly. NetworkChuck
runs about 206 with jokes and a sponsor break before the first build.
Metics Media is about 179 and shows no detectable on-screen action: it is
talk and comparison.

## What they do badly, and what we do better

- Derek's connection step pastes a server URL and token into a chat message.
  We keep tokens out of anything on screen and show a placeholder.
- He lets the agent reach every tool the n8n server offers. We expose one
  tool through an include line and say why.
- Nobody explains the test URL against the production URL, which is where a
  first connection usually fails. We make it a scene and a quiz.
- None names n8n's plain HTTP route or the Webhook node, so a viewer cannot
  choose a route. We give a three-way choice.
- NetworkChuck's SSH route runs another agent with broad rights. We do not
  teach it, and our script says nothing that depends on it.

## What we take (structure only)

1. Lead with what the link does, then how to build it.
2. Offer the quick official route before the manual one.
3. Put the test and the failure list close to the setup, since a first
   connection often needs a restart or the right URL.
4. Keep the example small: one workflow, one tool.
5. Say plainly which videos drift and what none of them covers.
