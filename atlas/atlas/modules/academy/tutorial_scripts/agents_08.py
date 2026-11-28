"""Agents #8: how to design tools an AI agent can use well.

Study note: docs/academy-references/agents_08.md

Approved by Miro 2026-11-24 (he authorised the review); facts re-read that day.

Written 2026-11-24 as a draft; a new entry (the series plan was used up after
#6). No live demo: bullets, a tool definition on screen, steps, two quizzes
and a pause.

Official pages read on 2026-11-24 (WebFetch):

  https://www.anthropic.com/engineering/writing-tools-for-agents
      ("When writing tool descriptions and specs, think of how you would
      describe your tool to a new hire on your team"; "Namespacing (grouping
      related tools under common prefixes) can help delineate boundaries
      between lots of tools"; tool results "should prioritize contextual
      relevance over flexibility, and eschew low-level technical
      identifiers"; "Prompts should be inspired by real-world uses and be
      based on realistic data sources and services"; "More tools don't
      always lead to better outcomes"; "building a few thoughtful tools
      targeting specific high-impact workflows"; errors should "clearly
      communicate specific and actionable improvements, rather than opaque
      error codes or tracebacks")
  https://www.anthropic.com/engineering/building-effective-agents
      ("Carefully craft your agent-computer interface (ACI) through thorough
      tool documentation and testing")

Reused from sibling module agents_05 (read 2026-10-18): "Poka-yoke your
tools. Change the arguments so that it is harder to make mistakes".

The order tools and the shop are our own example.

Also read 2026-11-24: platform.claude.com/docs/en/build-with-claude/context-windows
(everything in the request counts toward the context window, tool results
included; "context rot": as tokens grow, accuracy and recall degrade).
"""
from __future__ import annotations

from ..tutorial_series import (
    Episode,
    Scene,
    payoff_scene,
    plan,
    scripted,
)

# Level 3, number 8: the plan was used up at #6 (docs/academy-script-writer.md 2b).
PLANNED = plan(
    8, 3, "design-agent-tools",
    "How to Design Tools for an AI Agent: Descriptions, Results and Errors",
    "Tools an agent\ncan use",
    ("A tool is only as good as its description. How to name, describe and "
     "test the tools your agent uses, and what they should send back."),
    ("ai agents", "ai agent tools", "tool descriptions", "agent computer interface"),
    ("#aiagents", "#agenticai", "#artificialintelligence"),
    ("Why a tool's description matters",
     "Describe it like you would to a new hire",
     "Names that separate similar tools",
     "Results: what the agent needs, not raw IDs",
     "Errors that tell the agent what to do next",
     "Test tools with realistic tasks",
     "Recap checklist"))

TOOL = """find_order
  Look up one order by its order number
  (like 1042) and return its status
  and delivery date in plain words.

  order_number: the number from the
  customer's email, digits only."""

PICTURE = (
    "Describe it\nlike to a new hire",
    "Send back what\nit needs",
    "Test with\nreal tasks",
)

EPISODE: Episode = scripted(
    PLANNED,
    title_hook="AI Agent Tools",
    title_benefit="Design Them Well",
    result="Better tools",
    links=(("Writing tools for agents (Anthropic)",
            "https://www.anthropic.com/engineering/writing-tools-for-agents"),
           ("Building effective agents (Anthropic)",
            "https://www.anthropic.com/engineering/building-effective-agents")),
    ask="Which tool does your agent use most? Comment, and I'll cover it.",
    topic_said=("Today: how to design the tools an agent uses, so it picks the right "
                "one and gets back what it needs."),
    topic_shown="Tool descriptions, results, errors",
    payoff=payoff_scene(
        ("By the end, you'll be able to rewrite one of your agent's tools so it "
         "gets used the right way."),
        "name → description → result → error → test",
        items=PICTURE,
        caption="a tool is only as good as its description"),
    hook=Scene("What you'll learn", "hook", "Tools an agent can use", items=PICTURE,
               code=TOOL, caption="a tool is only as good as its description", said=(
                   ("Your agent has the right tools, and still picks the wrong one. "
                    "[curious] Often, the tool isn't the problem. The description is."),
                   ("Nothing to install today. Bring one tool your agent uses, or "
                    "one you'd like it to have."))),
    needs=("nothing to install", "one tool you'd like your agent to use"),
    scenes=(
        Scene("Why it matters", "bullets", "The agent reads, it doesn't guess", items=(
            "It chooses tools by their text",
            "A vague tool gets misused",
            "Anthropic calls this the agent-computer interface",
        ), said=(
            ("Why do descriptions matter? The model decides which tool to call "
             "by reading its name and its description. That's all it has. It can't peek inside your code, and it can't ask you what you meant."),
            ("So a vague tool gets used badly, or never used. The fix is "
             "usually words, not code. That's good news, because words are quick to change."),
            ("Anthropic calls this the agent-computer interface, and says to "
             "craft it with thorough documentation and testing."))),
        Scene("Describe it", "code", "A tool, described well", code=TOOL,
              marks=((0, 1), (1, 3), (4, 6)), caption="our own example", said=(
                  "Here's one tool. Start with a plain name that says what it does.",
                  ("Then the description. Anthropic's tip: think of how you'd "
                   "describe it to a new hire. Say what it does, and what comes "
                   "back, in plain words. No shorthand only your team understands."),
                  ("And describe each input. Where does the order number come "
                   "from? What shape is it? The agent can't see your screen, so spell it out. One example, like 1042, helps a lot."))),
        Scene("", "quiz", "Which description helps the agent most?", items=(
            "Gets data", "Looks up one order by its number and returns its status",
            "Order tool v2",
        ), answer=1, caption="Say what it does and returns.", said=(
            ("Quick quiz. Which description helps the agent most? Comment your "
             "answer."),
            "B. It says what the tool does, what it needs and what comes back.")),
        Scene("Fewer, clearer tools", "bullets", "Fewer, and clearly named", items=(
            "A few thoughtful tools beat many",
            "Group related tools with a prefix",
            "Make mistakes hard to make",
        ), said=(
            ("Next, how many tools. Anthropic says more tools don't always lead "
             "to better outcomes. Build a few thoughtful ones, aimed at the jobs "
             "that matter most. Two tools that overlap just make it hesitate."),
            ("When you do have many, group related ones under a shared prefix, "
             "like orders, then the action. The names then show where one tool "
             "ends and another begins. Less guessing for the agent, fewer wrong calls."),
            ("And make mistakes hard. Anthropic calls it poka-yoke: change the "
             "inputs, so the wrong call is difficult to make."))),
        Scene("", "subscribe", "Still with me?", said=(
            "If you want agents that pick the right tool, subscribe.",)),
        Scene("What comes back", "steps", "What the tool sends back", items=(
            "What the agent needs next",
            "Plain words, not raw IDs",
            "Errors that say what to try",
        ), said=(
            ("Now the answer a tool sends back. Send what the agent needs for "
             "its next step, and no more. Everything a tool sends back "
             "counts toward its context window."),
            ("Prefer plain words over low-level IDs. A customer's name and an "
             "order status help the agent far more than a long code."),
            ("And when something fails, say what to try. Anthropic suggests "
             "clear, actionable errors, rather than opaque codes or tracebacks. "
             "[emphasis] Order not found: check the number and try again. Now the agent knows its next move."))),
        Scene("", "pause", "Pause and try this", items=(
            "Pick one tool and rewrite it",
            "Add what it returns and one error",
        ), said=(
            ("Pause the video and pick one tool. Rewrite its name and "
             "description as if for a new colleague on their first day."),
            ("Then write one line on what it sends back, and one on what its "
             "error says. Resume when you've done it. Read it out loud. If it confuses you, it'll confuse the agent."))),
        Scene("Test the tools", "bullets", "Test them with real tasks", items=(
            "Use tasks from real work",
            "Watch which tool it picks",
            "Fix the words, then run again",
        ), said=(
            ("Last step: test the tools. Anthropic says test prompts should come "
             "from real-world uses, with realistic data."),
            ("Run a few and watch what the agent does. Did it pick the right "
             "tool? Did it pass the right input? Write down each miss, so you can compare after your change."),
            ("If it didn't, the fix is often a clearer description. "
             "[relaxed] Change the words, and run the same tasks again."))),
        Scene("", "quiz", "The agent keeps picking the wrong tool. First fix?", items=(
            "Delete the agent", "Rewrite the tool's description",
        ), answer=1, caption="Clearer words first.", said=(
            ("Last quiz. The agent keeps picking the wrong tool. What do you "
             "try first? Comment."),
            ("B. Rewrite the description, then run your tests again. It's "
             "often the quickest fix, and it costs nothing to try."))),
        Scene("Recap", "checklist", "Recap", items=(
            "Describe it like to a new hire",
            "Few tools, clear names",
            "Results the agent can use",
            "Errors that say what to try",
        ), said=(
            "Recap. Describe each tool like you would to a new colleague.",
            "Keep tools few, with clear names, and make mistakes hard to make.",
            "Send back what the agent needs, in plain words.",
            ("[happy] Make errors helpful, and test with real tasks. Better "
             "tools, better agent."))),
    ),
)
