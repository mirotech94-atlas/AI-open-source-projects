"""Agents #3: agentic AI, explained with a real workflow.

Written 2026-10-17 by the script writer (docs/academy-script-writer.md). No
live demo: no new recording can be made, so the "real workflow" is drawn as
a diagram, step by step, and the planned summary (which promised watching
an n8n run) is overridden so nothing promises a run on screen. The outline's
"where it went wrong once" is taught as the documented stopping limit, not
as a story.

Every fact was read on the official pages on 2026-10-17:

  www.anthropic.com/engineering/building-effective-agents
      ("At Anthropic, we categorize all these variations as agentic
      systems, but draw an important architectural distinction between
      workflows and agents"; "Workflows are systems where LLMs and tools are
      orchestrated through predefined code paths"; "Agents ... are systems
      where LLMs dynamically direct their own processes and tool usage";
      "an LLM enhanced with augmentations such as retrieval, tools, and
      memory"; agents "gain 'ground truth' from the environment at each
      step"; "pause for human feedback at checkpoints"; "stopping conditions
      (such as a maximum number of iterations)"; "They are typically just
      LLMs using tools based on environmental feedback in a loop"; "finding
      the simplest solution possible ... This might mean not building
      agentic systems at all"; agents "trade latency and cost for better
      task performance"; "extensive testing in sandboxed environments, along
      with the appropriate guardrails")
  docs.n8n.io/integrations/builtin/cluster-nodes/root-nodes/n8n-nodes-langchain.agent.md
      ("Connect a chat model and one or more tools, and the agent decides
      which tools to call to complete a task")
  docs.n8n.io/integrations/builtin/cluster-nodes/root-nodes/n8n-nodes-langchain.agent/tools-agent.md
      (Max Iterations: "the number of times the model should run to try and
      generate a good answer"; "Defaults to 10")
  docs.n8n.io/build/integrate-ai/ai-examples/human-in-the-loop-for-tools.md
      ("You can require human approval before an AI Agent executes a
      specific tool"; Approve: "The tool executes"; Deny: "The action is
      canceled and doesn't run")
"""
from __future__ import annotations

from ..tutorial_series import (
    AGENTS_PLANNED,
    Episode,
    Scene,
    payoff_scene,
    scripted,
)

PICTURE = (
    "Model\nanswers one prompt",
    "Agent\npicks its own steps",
    "Agentic system\nthe whole setup",
)

EPISODE: Episode = scripted(
    next(e for e in AGENTS_PLANNED if e.number == 3),
    summary=("Agentic AI without the buzzwords: a model, an agent and an agentic "
             "system told apart, then one n8n workflow drawn step by step, where the "
             "agent picks its tool, a limit stops it, and a person approves."),
    title_hook="Agentic AI",
    links=(("Building effective agents (Anthropic)",
            "https://www.anthropic.com/engineering/building-effective-agents"),
           ("n8n AI Agent node",
            ("https://docs.n8n.io/integrations/builtin/cluster-nodes/root-nodes/"
             "n8n-nodes-langchain.agent/")),
           ("Human review for tool calls (n8n)",
            ("https://docs.n8n.io/build/integrate-ai/ai-examples/"
             "human-in-the-loop-for-tools/"))),
    ask="Name one job in your week: fixed steps, or an agent? Tell me in the comments.",
    topic_said=("Today: agentic AI, with the buzzwords taken out: a model, an agent, "
                "and the system around them."),
    topic_shown="Model, agent, agentic system",
    payoff=payoff_scene(
        ("By the end, you'll tell a model, an agent and an agentic system apart, and "
         "know which one a job really needs."),
        "Workflow: fixed path · Agent: it decides the path",
        items=PICTURE,
        caption="three words, finally clear"),
    hook=Scene("What you'll learn", "hook", "Three words, one picture", items=PICTURE,
               code=("Model → one answer\n"
                     "Agent → a loop: think, use a tool, check\n"
                     "Agentic system → workflows and agents"),
               caption="three words, finally clear", said=(
                   ("Agentic AI is everywhere right now. [curious] But what does it "
                    "actually mean, for a real job?"),
                   ("Nothing to install today. All you need is one task from your week "
                    "to think about while we go."))),
    needs=("nothing to install", "one real task from your week to think about"),
    scenes=(
        Scene("Three words", "bullets", "Three words", items=(
            "Model: reads a prompt, writes an answer",
            "Agent: a model using tools, in a loop",
            "Agentic system: workflows and agents",
        ), said=(
            ("Let's start with three words. A model reads your prompt and writes an "
             "answer. One question in, one answer out."),
            ("An agent is a model that uses tools in a loop. It acts, looks at the "
             "result, and decides what to do next."),
            ("And agentic system is Anthropic's umbrella word. It covers both "
             "workflows and agents."))),
        Scene("Workflow or agent", "diagram", "Who decides the path?", items=(
            "Workflow\nthe path is written in advance",
            "Agent\nthe model directs its own steps",
            "Both\nare agentic systems",
        ), said=(
            ("So what's the difference inside that umbrella? In a workflow, the "
             "model and tools follow a path written in advance."),
            ("[emphasis] In an agent, the model directs its own process. It decides "
             "which tool to use, and when."),
            "Both count as agentic systems. The question is just: who picks the steps?")),
        Scene("", "quiz", "A model uses tools in a loop until it's done. That's…", items=(
            "A plain model", "An agent", "A spreadsheet",
        ), answer=1, caption="Tools, in a loop, guided by results: an agent.", said=(
            ("Quick quiz. A model uses tools in a loop, checking each result, until "
             "the job is done. What is it? Comment your answer."),
            "B, an agent. The loop is what makes it one.")),
        Scene("The building block", "bullets", "A model, with extras", items=(
            "Retrieval: it can look things up",
            "Tools: it can take a step",
            "Memory: it keeps what matters",
        ), said=(
            ("Every one of these starts from the same building block: a model with "
             "extras. First, retrieval, so it can look things up."),
            "Second, tools, so it can actually do a step, not just talk about it.",
            "And third, memory, so it keeps what matters from one step to the next.")),
        Scene("", "subscribe", "Still with me?", said=(
            "If this finally makes all that jargon click for you, subscribe.",)),
        Scene("A real workflow", "steps", "One job, drawn step by step", items=(
            "A customer email arrives",
            "The AI Agent picks a tool: look up the order",
            "It drafts a reply from what it found",
            "A person approves before it's sent",
        ), said=(
            ("Here's a real one, drawn as an n8n workflow. A customer emails to ask "
             "where their order is."),
            ("The AI Agent node has a chat model and some tools. It decides which "
             "tool to call: here, look up the order."),
            "It reads the result, and drafts a reply based on what it actually found.",
            ("Then a person approves. n8n can require human approval before an agent "
             "uses a tool, like sending the email. Deny it, and it doesn't run."))),
        Scene("What makes it agentic", "bullets", "What makes it agentic", items=(
            "It chose the step itself",
            "It checked the real result",
            "A limit stops it: Max Iterations",
        ), said=(
            ("So what made that agentic? First, nobody wrote look up the order into "
             "the path. The agent chose it."),
            ("Second, it checked the real result, the order data, before it wrote "
             "anything. Anthropic calls that ground truth from the environment."),
            ("Third, it has a limit. In n8n, Max Iterations caps how many times the "
             "model runs. The default is ten. [sighing] Without a limit, a confused "
             "agent could keep going."))),
        Scene("", "pause", "Pause and try this", items=(
            "Pick one job from your week",
            "Fixed steps, or does it need choices?",
        ), said=(
            "Pause the video and pick one job from your week.",
            ("Does it follow the same steps every time, or does it need choices? "
             "Resume when you've decided."))),
        Scene("Start simple", "bullets", "The question to ask first", items=(
            "Use the simplest thing that works",
            "Agents trade speed and cost for flexibility",
            "Test safely · keep a person in the loop",
        ), said=(
            ("Before you build one, ask: what's the simplest thing that works? "
             "Anthropic's own advice is to add complexity only when it's needed. "
             "Sometimes that means no agent at all."),
            ("Agents trade speed and cost for flexibility. That's worth it when you "
             "can't predict the steps."),
            ("And when you do build one, test it somewhere safe first, and keep a "
             "person approving anything that matters."))),
        Scene("", "quiz", "Same steps every time, Claude writes one reply. Agent?", items=(
            "Yes, an agent", "No, a workflow", "Just a model",
        ), answer=1, caption="A fixed path is a workflow, still agentic AI.", said=(
            ("Last quiz. A workflow runs the same steps every time, and Claude writes "
             "one reply in the middle. Is it an agent? Comment."),
            "B, it's a workflow. The path is fixed, even though a model is inside it.")),
        Scene("Recap", "checklist", "Recap", items=(
            "Model: one prompt, one answer",
            "Agent: tools in a loop, it picks the steps",
            "Agentic system: workflows and agents",
            "Start simple · add limits and approvals",
        ), said=(
            "Recap. A model takes one prompt and gives one answer.",
            "An agent uses tools in a loop, and picks its own steps.",
            "Agentic systems cover both workflows and agents.",
            ("[happy] And whichever you build, start simple, set a limit, and keep a "
             "person approving what matters."))),
    ),
)
