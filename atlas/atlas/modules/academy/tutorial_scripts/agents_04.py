"""Agents #4: how AI agents use tools, memory and planning.

Written 2026-10-17 by the script writer (docs/academy-script-writer.md). No
live demo: no new recording can be made, so the picture is diagrams,
bullets, one tool definition on screen, two quizzes and a pause.

Every fact was read on the official pages on 2026-10-17:

  platform.claude.com/docs/en/agents-and-tools/tool-use/overview
      (docs.claude.com redirects there; "Claude determines when to call a
      tool based on the user's request and the tool's description. It then
      returns a structured call that your application executes (client
      tools) or that Anthropic executes (server tools)"; "pass a tool with
      an input_schema, then execute the call when Claude returns a tool_use
      block"; "Your code executes the operation and sends back a
      tool_result"; the get_weather example: name, description,
      input_schema)
  platform.claude.com/docs/en/build-with-claude/context-windows
      (the context window is "all the text a language model can reference
      when generating a response ... a 'working memory' for the model";
      "Everything in the request counts toward the context window"; "As
      token count grows, accuracy and recall degrade")
  www.anthropic.com/engineering/building-effective-agents
      ("an LLM enhanced with augmentations such as retrieval, tools, and
      memory ... determining what information to retain"; agents "plan and
      operate independently"; "ground truth" from the environment at each
      step; "stopping conditions (such as a maximum number of iterations)";
      orchestrator-workers: "a central LLM dynamically breaks down tasks,
      delegates them to worker LLMs, and synthesizes their results")
  modelcontextprotocol.io/docs/getting-started/intro and
  modelcontextprotocol.io/docs/2026-07-28/learn/architecture
      ("an open-source standard for connecting AI applications to external
      systems"; "like a USB-C port for AI applications"; host, client,
      server; Tools, Resources, Prompts)
  docs.n8n.io/integrations/builtin/cluster-nodes/sub-nodes/n8n-nodes-langchain.memorybufferwindow.md
      ("Use the Simple Memory node to persist chat history in your
      workflow"; Context Window Length, "the number of previous interactions
      to consider")

The order-lookup tool on screen is our own example, shaped like the docs'
get_weather tool.
"""
from __future__ import annotations

from ..tutorial_series import (
    AGENTS_PLANNED,
    Episode,
    Scene,
    payoff_scene,
    scripted,
)

TOOL_JSON = """{
  "name": "find_order",
  "description": "Look up one order by its number.",
  "input_schema": {
    "type": "object",
    "properties": { "order_number": { "type": "string" } },
    "required": ["order_number"]
  }
}"""

PICTURE = (
    "Tools\nso it can act",
    "Memory\nso it remembers",
    "Planning\nso it gets there",
)

EPISODE: Episode = scripted(
    next(e for e in AGENTS_PLANNED if e.number == 4),
    title_hook="AI Agent Tools, Memory and Planning",
    links=(("Tool use with Claude",
            "https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview"),
           ("Context windows",
            "https://platform.claude.com/docs/en/build-with-claude/context-windows"),
           ("Building effective agents (Anthropic)",
            "https://www.anthropic.com/engineering/building-effective-agents"),
           ("What is MCP?", "https://modelcontextprotocol.io/docs/getting-started/intro")),
    ask="Which of the three would your business need most: tools, memory or planning?",
    topic_said=("Today: the three parts that make an AI agent useful, tools, memory "
                "and planning, drawn one at a time."),
    topic_shown="Tools, memory and planning",
    payoff=payoff_scene(
        ("By the end, you'll be able to draw how an agent asks for a tool, what it "
         "remembers, and how it breaks a goal into steps."),
        "Goal → plan → tool → result → next step → done",
        items=PICTURE,
        caption="three parts, one loop"),
    hook=Scene("What you'll learn", "hook", "Three parts", items=PICTURE,
               code=("Tools → it can do things\n"
                     "Memory → it keeps what matters\n"
                     "Planning → it breaks a goal into steps"),
               caption="three parts, one loop", said=(
                   ("A model on its own can only talk. [curious] So what turns it into "
                    "something that gets work done?"),
                   ("Nothing to install today. All you need is a pen, if you'd like to "
                    "draw along."))),
    needs=("nothing to install", "a pen and paper, to draw along"),
    scenes=(
        Scene("Tools: the request", "diagram", "How a model asks for a tool", items=(
            "You describe the tool\nname, description, inputs",
            "The model asks\na tool call, with the inputs",
            "Your code runs it\nand sends the result back",
        ), said=(
            ("Tools first. You describe each tool to the model: a name, a short "
             "description, and the inputs it takes."
             " Think of it as a menu the model can order from."),
            ("When a request needs it, the model doesn't run anything. It replies "
             "with a tool call: which tool, and with what inputs."),
            ("[emphasis] Your code runs the tool, and sends the result back. Then the "
             "model carries on, with real data in hand."
             " That's the whole trick."))),
        Scene("A tool, on paper", "code", "One tool definition", code=TOOL_JSON,
              marks=((1, 2), (2, 3), (3, 8)), caption="shaped like the docs' example",
              said=(
                  "Here's what a tool looks like. A name: find order.",
                  ("A description. The model uses it to decide when to call the "
                   "tool, so write it like you'd explain it to a new colleague."),
                  ("And the inputs, as a small schema: here, one order number, and "
                   "it's required."
             " Required means the model has to give one every time it asks."))),
        Scene("", "quiz", "Who actually runs the tool?", items=(
            "The model", "Your code or app", "Nobody",
        ), answer=1, caption="The model asks; your code runs it.", said=(
            ("Quick quiz. When a model makes a tool call, who actually runs the tool? "
             "Comment your answer."),
            ("B, your code. The model only asks. Some tools run on the provider's "
             "side, but even then, the model doesn't run them itself."))),
        Scene("Memory", "bullets", "Short-term and long-term memory", items=(
            "Short-term: the context window",
            "It fills up, and recall gets worse",
            "Long-term: saved, then looked up again",
        ), said=(
            ("Now memory. Short-term memory is the context window: everything the "
             "model can see right now, like a working memory."
             " That's different from what it learned in training."),
            ("Every message, every tool result counts towards it. [sighing] And as it "
             "fills up, the model gets worse at recalling things."),
            ("Long-term memory means saving what matters somewhere else, and looking "
             "it up again later. In n8n, the Simple Memory node keeps the chat history "
             "for you."))),
        Scene("", "subscribe", "Still with me?", said=(
            "If these diagrams are helping, subscribe for the next one.",)),
        Scene("Planning", "diagram", "Breaking a goal into steps", items=(
            "Goal\n'answer this customer'",
            "Steps\nfind the order, check it, draft",
            "Check each result\nand stop when done",
        ), said=(
            ("Third, planning. An agent starts from a goal, like: answer this "
             "customer."),
            ("It breaks that into steps: find the order, check what it says, draft a "
             "reply. A bigger setup can even hand steps to other models."),
            ("After each step it checks the real result, and adjusts. And a limit, "
             "like a maximum number of tries, makes sure it stops."
             " Without one, a confused agent could keep going for a long time."))),
        Scene("", "pause", "Pause and try this", items=(
            "Write one goal from your week",
            "Split it into three steps",
        ), said=(
            ("Pause the video and write down one goal from your own week."
             " Something you do often."),
            "Then split it into three steps an agent would take. Resume when you have them.")),
        Scene("Where MCP fits", "diagram", "Where MCP fits", items=(
            "Host\nthe AI app you use",
            "Client\none per connection",
            "Server\noffers tools, resources, prompts",
        ), said=(
            ("So where does MCP fit? It's an open standard for connecting AI apps to "
             "other systems. Think of a USB-C port for AI."),
            ("The app you use is the host. It opens one client connection for each "
             "server."
             " Claude Code and Claude Desktop are both hosts."),
            ("And each server offers tools, along with resources and prompts. [excited] "
             "Write a tool once, and any MCP app can use it."
             " That's why it keeps coming up."))),
        Scene("", "quiz", "The model forgets things in a very long chat. Why?", items=(
            "It's tired", "The context window is filling up", "Tools are broken",
        ), answer=1, caption="More in the window, worse recall.", said=(
            ("Last quiz. In a very long chat, the model starts missing things. Why? "
             "Comment."),
            ("B. The context window is filling up, and recall gets worse as it does."
             " A fresh chat, or saved notes, helps."))),
        Scene("Recap", "checklist", "Recap", items=(
            "Tools: the model asks, your code runs",
            "Memory: the window now, saved notes later",
            "Planning: steps, checks, a limit",
            "MCP: one standard way to plug in tools",
        ), said=(
            "Recap. With tools, the model asks, and your code does the work.",
            "Memory is the context window now, and saved notes for later.",
            "Planning breaks a goal into steps, checks each one, and stops at a limit.",
            ("[happy] And MCP is the standard plug for tools. Three parts, one loop, "
             "and now you can draw it."))),
    ),
)
