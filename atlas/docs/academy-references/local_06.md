# Study note: local_06

Topic searched: "hermes agent mcp setup" (2026-10-19). Written in our own words; no transcript is kept in the repo.

## Videos studied

- https://www.youtube.com/watch?v=qAss7qQk9W4 -- Teacher's Tech, 66,691 views, 21:19, read 2026-10-19. 181 words/min; first on-screen action at 355 s (transcript heuristic); 17 chapters.
- https://www.youtube.com/watch?v=4sAmpcSOVEw -- Adrian Twarog, 59,023 views, 22:18, read 2026-10-19. 209 words/min; first on-screen action at 36 s (transcript heuristic); 27 chapters.
- https://www.youtube.com/watch?v=3Pr9BPjxICE -- Jake's Tech, 37,168 views, 16:49, read 2026-10-19. 244 words/min; first on-screen action at 170 s (transcript heuristic); 16 chapters.

## How they open

None of the three is about MCP alone: all are general Hermes Agent setup
videos, and only Adrian Twarog's has an MCP chapter, so the match is
partial. Teacher's Tech opens on what the agent will do for you (sort
files, draft content, remember preferences) and promises the free plan
first. Twarog opens on Hermes versus another open source agent he finds
unreliable, then the licence and the plan for the video. Jake's Tech opens
on running your own agent from your phone, and that he was disappointed by
an earlier tool.

## The order they explain things

- Teacher's Tech: what the agent is, install, a free model, asking it what
  tools it has, finding the edge of the free plan, the safety page (manual
  approval for commands, file checkpoints), real tasks in one scoped
  folder, then a long section on what can go wrong and the guardrails.
- Twarog: install, the three setup paths, a provider, a chat channel, the
  terminal and the web dashboard, then a tour of every panel; MCP comes
  late: add a server by URL, connect it, see its tools listed, ask a
  question that triggers one tool, then a note that an external service
  can limit which actions the agent gets (find and draft, not send or
  delete). Cloud hosting last.
- Jake's Tech: why an agent runtime differs from a chat window, a simple
  picture (the model decides, Hermes acts, the chat app is the interface),
  install, a model gateway key, a bot locked to his own account, memory
  and skills, and a short security warning at the end.

## Examples and analogies

- A fictional small coffee roaster used for every task, so demos feel like
  real work (Teacher's Tech).
- Reading the latest email through one connected tool server (Twarog).
- The model as the brain, Hermes as the body that acts, the chat app as
  the mouth and ears (Jake's Tech, in his own framing).

## Beginner mistakes they address

- Leaving approval on the automatic default during the first week.
- Letting the agent work across the whole disk instead of one folder.
- Adding community skills or servers without reading what they do.
- Giving the agent a logged-in browser or a production inbox early on.
- Leaving a chat bot open to anyone instead of locking it to your account.
- Connecting so many tool servers that the agent gets confused (Twarog).

## Pacing

All three are long (17 to 22 minutes) and fast, about 180 to 245 words a
minute. Twarog has many chapters, one per dashboard panel, which makes the
MCP part easy to find but brief. Teacher's Tech moves slowest and repeats
its safety habits, which suits beginners.

## What they do badly, and what we do better

- MCP gets a few minutes at most, and through a paid third-party bridge.
  We give it the whole lesson and use the config file Hermes documents.
- Limiting tools is left to an outside service's settings. We show Hermes'
  own include and exclude lists, so the limit lives in your config.
- None explains how to find out why a server's tools are missing. We name
  the agent log and the test command.
- Pricing, plan comparisons and hosting coupons take up time. We name no
  price.
- One video says a model maker blocks its models in third-party agents; we
  make no claim we didn't read in official docs.

## What we take (structure only)

1. Open on the result: two servers' tools listed, and nothing else.
2. One picture of what MCP adds before any config.
3. The config, one server at a time: local first, then remote.
4. Guardrails as part of the build (include, exclude), not an appendix.
5. A stuck section with the two checks that tell you what went wrong.
