"""Agents #7: one AI agent with tools, or several agents working together.

Study note: docs/academy-references/agents_07.md

Approved by Miro 2026-11-24 (he authorised the review); facts re-read that day.

Written 2026-11-24 as a draft; a new entry (the series plan was used up after
#6). No live demo: bullets, diagrams, steps, two quizzes and a pause.

Official pages read on 2026-11-24 (WebFetch):

  https://www.anthropic.com/engineering/building-effective-agents
      ("we recommend finding the simplest solution possible, and only
      increasing complexity when needed"; orchestrator-workers: "a central
      LLM dynamically breaks down tasks, delegates them to worker LLMs, and
      synthesizes their results"; parallelization: "LLMs can sometimes work
      simultaneously on a task and have their outputs aggregated
      programmatically")
  https://www.anthropic.com/engineering/writing-tools-for-agents
      ("More tools don't always lead to better outcomes"; "building a few
      thoughtful tools targeting specific high-impact workflows")
  https://docs.n8n.io/build/integrate-ai/ai-examples/human-in-the-loop-for-tools/
      (review steps in a sub-agent "work correctly" when an AI Agent is used
      as a tool for another agent)

Reused from sibling module agents_05 (read 2026-10-18): "The autonomous
nature of agents means higher costs, and the potential for compounding
errors".

The shop, its support agent and its helpers are our own example.

Also read 2026-11-24: anthropic.com/engineering/building-effective-agents
(agentic systems "trade latency and cost for better task performance"; autonomy
"means higher costs" and "the potential for compounding errors"; find "the
simplest solution possible", add complexity only when simpler solutions fall
short).
"""
from __future__ import annotations

from ..tutorial_series import (
    Episode,
    Scene,
    payoff_scene,
    plan,
    scripted,
)

# Level 3, number 7: the plan was used up at #6 (docs/academy-script-writer.md 2b).
PLANNED = plan(
    7, 3, "one-agent-or-many",
    "One AI Agent or Several? How to Choose, for Beginners",
    "One agent,\nor many?",
    ("When one agent with a few tools is enough, when splitting the work across "
     "helper agents makes sense, and why to start simple."),
    ("ai agents", "multi agent", "orchestrator workers", "ai agent design"),
    ("#aiagents", "#agenticai", "#artificialintelligence"),
    ("Start with the simplest thing that works",
     "One agent, a few well-chosen tools",
     "Orchestrator and workers: one splits, others do",
     "Side by side: pieces that don't need each other",
     "In n8n: an agent used as a tool for another agent",
     "The cost of more agents",
     "Recap checklist"))

PICTURE = (
    "Start with\none agent",
    "Split only\nwhen it helps",
    "Every extra agent\nhas a cost",
)

EPISODE: Episode = scripted(
    PLANNED,
    title_hook="AI Agents, One or Many",
    title_benefit="One Rule",
    result="One rule",
    links=(("Building effective agents (Anthropic)",
            "https://www.anthropic.com/engineering/building-effective-agents"),
           ("Writing tools for agents (Anthropic)",
            "https://www.anthropic.com/engineering/writing-tools-for-agents"),
           ("Human-in-the-loop for tools (n8n)",
            ("https://docs.n8n.io/build/integrate-ai/ai-examples/"
             "human-in-the-loop-for-tools/"))),
    ask="Which job in your business would you hand to a helper agent first? Comment.",
    topic_said=("Today: when one AI agent is enough, and when it makes sense to "
                "split the work across several."),
    topic_shown="One agent or several",
    payoff=payoff_scene(
        ("By the end, you'll have one simple rule for choosing between a single "
         "agent and a team of helpers."),
        "One agent first → split only when it helps",
        items=PICTURE,
        caption="start simple"),
    hook=Scene("What you'll learn", "hook", "One agent, or a team?", items=PICTURE,
               code=("One agent → a few good tools\n"
                     "Several agents → one plans, others do"),
               caption="start simple", said=(
                   ("More agents must be better, right? [curious] Not always, and "
                    "the reason may save you a lot of work."),
                   ("Nothing to install today. Just think of one job you'd like an "
                    "agent to do."))),
    needs=("nothing to install", "one job you'd like an agent to do"),
    scenes=(
        Scene("Start simple", "bullets", "Start with the simplest thing", items=(
            "Anthropic: find the simplest solution",
            "Add complexity only when needed",
            "One agent with a few tools first",
        ), said=(
            ("Here's the advice from Anthropic's own guide. Find the simplest "
             "solution possible, and only add complexity when you need it."),
            ("It's easy to get excited and build a whole team of agents. A "
             "simple setup is easier to test, easier to fix, and cheaper to run."),
            ("[confident] So the starting point is one agent, with a few tools. Many jobs "
             "never need more than that. If it works, you're done, and that's a good day."))),
        Scene("Orchestrator and workers", "diagram", "One plans, others do", items=(
            "The orchestrator\nbreaks the job into parts",
            "The workers\ndo one part each",
            "The orchestrator\ncombines the results",
        ), said=(
            ("So when would you split? Anthropic describes one pattern: "
             "orchestrator and workers. A central model breaks a task into parts, "
             "as it goes."),
            ("It hands each part to a worker model, and each worker does one "
             "small job."),
            ("Then the central one gathers the results, and puts them "
             "together. Think of a manager and a few specialists. The manager never does the small jobs itself."))),
        Scene("", "quiz", "A support agent only answers order questions. Add helpers?",
              items=("Yes, five of them", "No: one agent is enough", "Add ten tools"),
              answer=1, caption="Simplest thing that works.", said=(
                  ("Quick quiz. Your agent only answers questions about orders. "
                   "Do you build a team of helpers? Comment your answer."),
                  "B. One agent is enough. Extra helpers would only add things to break, and more to check.")),
        Scene("When to split", "bullets", "Signs it may be time to split", items=(
            "One agent has too many tools",
            "The jobs are quite different",
            "Pieces can run side by side",
        ), said=(
            ("When might you split? First, if one agent has too many tools. "
             "Anthropic's own advice on tools is that more tools don't always "
             "lead to better outcomes."),
            ("Second, if the jobs are very different. Answering a customer and "
             "checking a stock list are not the same skill, so a helper for each "
             "can keep each one simple, and easier to test."),
            ("Third, if pieces don't depend on each other. Anthropic notes models "
             "can sometimes work at the same time, and the results are combined "
             "afterwards. Say, checking three suppliers at once, instead of one after another."))),
        Scene("", "subscribe", "Still with me?", said=(
            "If you're building agents for your business, subscribe.",)),
        Scene("In n8n", "steps", "In n8n: an agent as a tool", items=(
            "Build the helper agent first",
            "Connect it as a tool of the main agent",
            "Keep approvals on risky tools",
        ), said=(
            ("In n8n, you can use one AI Agent as a tool for another. So you "
             "build the helper first, and test it alone, the way you would any agent. Only then does it join the team."),
            ("Then you connect it to the main agent as one of its tools. The "
             "main agent decides when to call it, just like any other tool."),
            ("And the human approval steps for risky tools still work when the "
             "tool sits inside a helper agent. Keep them on anything that "
             "sends, changes or deletes."))),
        Scene("", "pause", "Pause and try this", items=(
            "Write your job in one sentence",
            "Count the different skills it needs",
        ), said=(
            ("Pause the video and write your job in one sentence. Something "
             "like: answer customers about their orders."),
            ("Now count the different skills it needs. One or two? Stay with "
             "one agent. Two or three? Maybe split. Resume when you've counted."))),
        Scene("The cost of more", "bullets", "What each extra agent costs", items=(
            "More to test and to read in logs",
            "Mistakes can pass from agent to agent",
            "More runs, so a bigger bill",
        ), said=(
            ("Now the cost. Each extra agent is one more thing to test, and "
             "one more place to look when something goes wrong."),
            ("Anthropic warns that agents bring the potential for compounding "
             "errors. [emphasis] With several in a chain, one wrong answer can "
             "feed the next."),
            ("And each one makes its own model calls, so the work costs more "
             "to run. Split because it helps, never because it looks clever. A small setup you understand beats a big one you don't."))),
        Scene("", "quiz", "What do you do first when a job feels too big?", items=(
            "Add more agents", "Try one agent with a few tools",
        ), answer=1, caption="Start simple, then split.", said=(
            ("Last quiz. A job feels too big for one agent. What do you try "
             "first? Comment."),
            ("B. One agent with a few well-chosen tools. Split only if that "
             "really isn't enough, and you've tested it to be sure."))),
        Scene("Recap", "checklist", "Recap", items=(
            "Start with the simplest thing",
            "One agent, a few well-chosen tools",
            "Split when tools or jobs pile up",
            "Every agent adds cost and risk",
        ), said=(
            "Recap. Start with the simplest thing that works.",
            "One agent, with a few well-chosen tools, goes a long way.",
            "Split when the tools or jobs pile up, or pieces can run apart.",
            ("[happy] And remember every extra agent costs something. Keep it as "
             "small as the job allows."))),
    ),
)
