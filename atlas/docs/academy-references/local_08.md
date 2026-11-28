# Study note: local_08

Topic searched: "Hermes Agent Telegram bot setup" (2026-10-26). Written in our own words; no transcript is kept in the repo.

## Videos studied

- https://www.youtube.com/watch?v=wETSpF4ef6E -- "How to Use Hermes AI Agent", Kevin Stratvert, 39,970 views, 10:13, read 2026-10-26. 196 words/min; first on-screen action at 115 s (transcript heuristic); 13 chapters.
- https://www.youtube.com/watch?v=3Pr9BPjxICE -- "How to Setup Hermes Agent with OpenRouter (Step-by-Step Guide)", Jake's Tech, 37,891 views, 16:49, read 2026-10-26. 244 words/min; first on-screen action at 170 s (transcript heuristic); 16 chapters.
- https://www.youtube.com/watch?v=AHHYCmpvhMQ -- "How to Setup Hermes Agent on Hostinger VPS (Telegram & API Guide)", MMX, 4,636 views, 15:51, read 2026-10-26. 186 words/min; first on-screen action at 0 s (transcript heuristic); 0 chapters.

## How they open

Kevin Stratvert opens with a short list of what the viewer will cover
(install, memory, skills, automation) and introduces himself in one line.
Jake's Tech opens with the end goal, controlling your own agent from a
phone, then spends a while on why the agent differs from a chat window.
MMX skips any introduction and goes straight to a hosting sign-up page.
Drift note: Kevin's video only touches messaging in its last seconds, and
MMX is mostly a rented-server walkthrough; only Jake's video is mainly about
the Telegram bot.

## The order they explain things

- Kevin Stratvert: desktop installer, run a model on the laptop, a first file
  task, connect a browser, review and answer comments, memory, a saved skill,
  a scheduled job, and last a one-click messaging screen.
- Jake's Tech: why an agent beats a chat window, a diagram of the parts
  (model provider, agent, chat app, and a bridge between them), one-line
  install, router key and a cheap model, the setup wizard with a bot token and
  your own user ID, then the bridge step, restart, a first message, and a
  tour of the personality, memory and skills folders.
- MMX: rent a small server, open its web terminal, run the messaging setup
  wizard, make the bot in BotFather, paste the token, paste your user ID, then
  install the gateway so the bot stays online.

## Examples and analogies

- The parts of the system as a brain, a doer, a chat window and a bridge
  (Jake's Tech).
- A real working use: drafting replies to channel comments, then approving
  them in chat (Kevin Stratvert).
- A hidden token field that shows nothing as you paste, explained as a
  safety feature (MMX).

## Beginner mistakes they address

- Setting up the bot and token but never starting the bridge, so the bot
  never answers. Jake says this cost him hours.
- Leaving the bot open to anyone, rather than limiting it to your own ID.
- Choosing expensive models by default and running up a bill.
- Hiding or leaking the token while recording.
- Giving an agent access to a real mailbox, where a planted instruction
  could cause harm.

## Pacing

Kevin Stratvert is moderate at about 196 words a minute and keeps every step
on screen. Jake's Tech is the fastest at about 244, with long explanations
before the first action at nearly three minutes. MMX is about 186 and slow in
the good sense: it names each key to press and what the screen will show.

## What they do badly, and what we do better

- None teaches Slack. We cover it, with its two token types and the empty
  allowed-users line that denies everyone.
- Pairing codes for adding a new person are never mentioned. We include them.
- Two of the three mix in hosting sales or a router sign-up, which delays the
  bot. We stay on the bot itself.
- Jake treats the allowed-user line as a side note. We make it a whole scene
  and warn against the allow-everyone switch.
- Jake and MMX go straight to a background service. We show foreground
  first, then the service, so the viewer sees the bot's messages.

## What we take (structure only)

1. State the goal first: message your own agent from a phone.
2. Show where the bridge sits between chat app and agent, and say the bot
   stays silent without it.
3. Create the bot, then put the token in the private file, then name your
   own ID.
4. Run it in the foreground for a first test, then as a service.
5. Make allowed users the safety spine of the lesson.
