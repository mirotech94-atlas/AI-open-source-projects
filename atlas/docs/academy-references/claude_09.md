# Study note: claude_09

Topic searched: "Claude Code permissions" (2026-11-25). Written in our own words; no transcript is kept in the repo.

## Videos studied

- "How auto mode works with Claude Code", Claude, https://www.youtube.com/watch?v=b8SV4U6fEIc -- 4,020,159 views, 5:42, read 2026-11-25. 139 words/min; first on-screen action at not detectable; 4 chapters.
- "STOP Using Bypass Permissions, Use This New Feature Instead", Nate Herk | AI Automation, https://www.youtube.com/watch?v=pkSxISewcw8 -- 119,307 views, 5:41, read 2026-11-25. 169 words/min; first on-screen action at 117 s (transcript heuristic); 6 chapters.
- "Claude Code Tutorial #4 - Tools & Permissions", Net Ninja, https://www.youtube.com/watch?v=TU0ZcDFq0e0 -- 114,624 views, 4:55, read 2026-11-25. 210 words/min; first on-screen action at not detectable; 4 chapters.

## How they open

Anthropic's own video opens on a number: nearly all permission prompts get
approved, so people tire of them. Nate Herk opens by showing the tiring
loop itself, Claude stopping at every edit while he wants to step away.
Net Ninja opens on the built-in tools, saying you never need to name them.
Two of the three are about auto mode, not rules and modes in general.

## The order they explain things

- Anthropic: why approving everything wears you out, what the classifier
  looks at (your messages and Claude's actions, not Claude's own reasoning),
  what happens when it says no, the scan of tool results for hidden
  instructions, which actions skip the check, then how to describe your
  environment and add allow, soft deny and hard deny guidance.
- Nate Herk: the ask-before-edits loop, the risky bypass mode, his own
  settings file with allow, deny and ask lists, then auto mode as the middle
  path, how to switch it on, and its limits (research preview then, more
  cost, not for shared machines).
- Net Ninja: the built-in tools and which need permission, approve an edit
  three times, choose the "don't ask again" option for a git command and see
  the settings file appear, then a keyboard shortcut for accepting edits for
  one session.

## Examples and analogies

- Reviewing your own pull request, as the reason Claude must not approve
  its own actions (Anthropic).
- A force push to main refused, so Claude tries a new branch instead
  (Anthropic).
- Remote branches removed after a request that covered only local cleanup
  (Anthropic).
- Three edits to one CSS file meaning three approval prompts (Net Ninja).
- A per-project settings file copied from project to project, replaced by
  one mode switch (Nate Herk).

## Beginner mistakes they address

- Approving every prompt without reading it, out of fatigue.
- Switching to the mode that skips every prompt on a machine that matters.
- Treating auto mode as proof of safety; it lowers risk, it does not remove
  it, and it is not for the highest-stakes work.
- Not knowing that the "don't ask again" choice writes a rule to a local
  settings file, which should not be committed.
- Assuming the classifier knows your company's servers; by default only the
  working folder and its git remotes count as internal.

## Pacing

Anthropic's video is the slowest, about 139 words a minute, with clear
chapters. Nate Herk is quick and conversational at about 169. Net Ninja is
the fastest here at about 210 but short, and he shows each prompt on
screen. All three are under six minutes.

## What they do badly, and what we do better

- Anthropic and Nate Herk centre on one mode and say little about writing
  allow, ask and deny rules; Nate shows his file only briefly. We teach the
  rule format, the order deny, ask, allow, and the star.
- Net Ninja shows the prompt clicks but not the three rule kinds, the order
  they are checked in, or the other modes. We cover all of that.
- Nate's talk of availability dates from the research preview and is out of
  date; Net Ninja's shortcut talk is specific to one keyboard. We avoid
  dated availability claims.
- Only Anthropic's video says that a rule cannot catch a command written
  another way. We keep that honest limit and point to a hook.
- Our first script left auto mode out of the mode list. The study showed it
  is a headline mode, so we added one line, checked against the current
  permission-modes page.

## What we take (structure only)

1. Open on prompt fatigue: you click yes without reading.
2. What asks by default and what doesn't, and what "don't ask again" saves.
3. Allow, ask and deny, and which wins.
4. A settings file with a few rules, and the limits of pattern matching.
5. The modes in order of trust, ending with the one that skips prompts and
   where it is safe.
6. Rules are enforced by Claude Code itself, not by the model.
