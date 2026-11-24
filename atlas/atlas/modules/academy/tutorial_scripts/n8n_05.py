"""n8n #5: a first AI agent in n8n, with a tool and memory.

Written 2026-10-17 by the script writer (docs/academy-script-writer.md). No
live demo: no new recording can be made, so the picture is steps, a
diagram, bullets, two quizzes and a lab.

Every product fact was read on the official n8n docs on 2026-10-17:

  docs.n8n.io/integrations/builtin/cluster-nodes/root-nodes/n8n-nodes-langchain.agent.md
      ("Connect a chat model and one or more tools, and the agent decides
      which tools to call to complete a task"; "You must connect at least
      one tool sub-node to an AI Agent node"; "All AI Agent nodes now work
      as a Tools Agent")
  .../n8n-nodes-langchain.agent/tools-agent.md
      (Prompt: "Take from previous node automatically" or "Define below";
      System Message; Max Iterations "Defaults to 10"; Return Intermediate
      Steps: "include intermediate steps the agent took in the final
      output")
  .../n8n-nodes-langchain.agent/common-issues.md
      ("A Chat Model sub-node must be connected error": "click the + Chat
      Model button"; "No prompt specified error": change Prompt to Define
      below)
  docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-langchain.chattrigger.md
      ("Use the Chat Trigger node when building AI workflows for chatbots";
      "n8n recommends connecting both the Chat trigger and Agent to the same
      memory sub-node")
  docs.n8n.io/integrations/builtin/cluster-nodes/sub-nodes/n8n-nodes-langchain.memorybufferwindow.md
      ("Use the Simple Memory node to persist chat history in your
      workflow"; Context Window Length: "the number of previous
      interactions to consider for context"; not for queue mode)
  docs.n8n.io/integrations/builtin/cluster-nodes/sub-nodes/n8n-nodes-langchain.toolcalculator.md
      ("a tool that allows an agent to run mathematical calculations")
  docs.n8n.io/build/integrate-ai/ai-examples/human-in-the-loop-for-tools.md
      ("You can require human approval before an AI Agent executes a
      specific tool"; Deny: "The action is canceled and doesn't run")

A lab may hold only credential-free nodes (tests/test_academy_labs.py), and
an agent needs a chat model credential, so the lab file is a starter: a
Manual Trigger and Edit Fields (Set v3.4) holding the question; the viewer
adds the AI Agent, their own chat model and the Calculator tool. The
outline's "expect a tool call in the log" is taught through Return
Intermediate Steps, the option the docs describe.
"""
from __future__ import annotations

from ..tutorial_series import (
    N8N_PLANNED,
    Episode,
    Scene,
    lab_scene,
    payoff_scene,
    scripted,
)

LAB_JSON = """{
  "name": "ATLAS Academy lab: first agent (starter)",
  "nodes": [
    { "name": "Start", "type": "n8n-nodes-base.manualTrigger",
      "typeVersion": 1, "position": [0, 0], "parameters": {} },
    { "name": "Question", "type": "n8n-nodes-base.set",
      "typeVersion": 3.4, "position": [240, 0],
      "parameters": { "assignments": { "assignments": [
          { "id": "lab5-question", "name": "chatInput",
            "value": "What is 1234 times 5678? Use your calculator.",
            "type": "string" } ] },
        "options": {} } }
  ],
  "connections": {
    "Start": { "main": [[{ "node": "Question", "type": "main", "index": 0 }]] }
  }
}"""

LAB_FILE = "n8n-first-agent-starter.json"
LAB_FILES: dict[str, str] = {LAB_FILE: LAB_JSON + "\n"}

PICTURE = (
    "Chat Trigger\na question comes in",
    "AI Agent\npicks a tool",
    "Answer\nand it remembers",
)

EPISODE: Episode = scripted(
    next(e for e in N8N_PLANNED if e.number == 5),
    title_hook="n8n AI Agents",
    links=(("AI Agent node",
            ("https://docs.n8n.io/integrations/builtin/cluster-nodes/root-nodes/"
             "n8n-nodes-langchain.agent/")),
           ("AI Agent common issues",
            ("https://docs.n8n.io/integrations/builtin/cluster-nodes/root-nodes/"
             "n8n-nodes-langchain.agent/common-issues/")),
           ("Simple Memory node",
            ("https://docs.n8n.io/integrations/builtin/cluster-nodes/sub-nodes/"
             "n8n-nodes-langchain.memorybufferwindow/")),
           ("Human review for tool calls",
            ("https://docs.n8n.io/build/integrate-ai/ai-examples/"
             "human-in-the-loop-for-tools/"))),
    ask="What's the first job you'd give an n8n agent? Tell me in the comments.",
    topic_said=("Today: our first AI agent in n8n, with a chat model, a tool it can "
                "call, and memory."),
    topic_shown="Your first n8n AI agent",
    payoff=payoff_scene(
        ("By the end, you'll have an agent you can chat with, that picks a tool by "
         "itself and remembers what you said a minute ago."),
        "You: what's 1234 × 5678? → Agent calls Calculator → answers",
        items=PICTURE,
        caption="a model, a tool, a memory"),
    hook=Scene("What we'll build", "hook", "An agent in four nodes", items=PICTURE,
               code=("Chat Trigger → AI Agent\n"
                     "  + chat model · + Calculator · + Simple Memory"),
               caption="a model, a tool, a memory", said=(
                   ("A workflow follows your steps. An agent chooses its own. [curious] "
                    "So how do you build one in n8n?"),
                   ("All you need is n8n and one chat model credential, from the "
                    "provider you like. And there's a starter lab file at the end."))),
    needs=("n8n, Cloud or self-hosted", "one chat model credential",
           "the free starter lab file"),
    scenes=(
        Scene("The pieces", "diagram", "Four pieces", items=(
            "Chat Trigger\nwhere messages come in",
            "AI Agent\nthe one that decides",
            "Sub-nodes\nmodel, tools, memory",
        ), said=(
            ("Here are the pieces. A Chat Trigger, where your messages come in. It's "
             "made for chatbots."
             " And while you build, you can chat with it right inside n8n."),
            ("Then the AI Agent node. It's the one that decides what to do with each "
             "message."),
            ("And under the agent hang its sub-nodes: a chat model, one or more tools, "
             "and memory."))),
        Scene("Step 1: agent and model", "steps", "Step 1: the agent and its model", items=(
            "Add a Chat Trigger",
            "Add the AI Agent after it",
            "+ Chat Model: pick yours, add the credential",
        ), said=(
            "Create a workflow and add a Chat Trigger.",
            "After it, add the AI Agent node.",
            ("Under the agent, click the plus for Chat Model, pick the provider you "
             "use, and add your credential once."
             " The model does the thinking; the agent decides how to use it."))),
        Scene("Step 2: a tool", "steps", "Step 2: give it a tool", items=(
            "+ Tool: Calculator",
            "At least one tool is required",
            "The agent decides when to use it",
        ), said=(
            ("Next, click the plus for Tool, and add the Calculator. It lets the agent "
             "do real maths, instead of guessing."),
            ("[emphasis] An AI Agent needs at least one tool connected. Without one, "
             "it won't run."),
            "You don't tell it when to calculate. It decides, from your question.")),
        Scene("", "quiz", "An AI Agent node has a model but no tool. It…", items=(
            "Runs fine", "Needs at least one tool", "Uses the internet instead",
        ), answer=1, caption="At least one tool sub-node must be connected.", said=(
            ("Quick quiz. Your AI Agent has a chat model, but no tool at all. What "
             "happens? Comment your answer."),
            "B. It needs at least one tool connected before it works.")),
        Scene("Step 3: memory", "steps", "Step 3: give it a memory", items=(
            "+ Memory: Simple Memory",
            "Context Window Length: how many turns",
            "Same memory for trigger and agent",
        ), said=(
            ("Now memory. Click the plus for Memory, and add Simple Memory. It keeps "
             "the chat history inside your workflow."),
            ("Context Window Length is how many earlier messages it looks back at."
             " A small number keeps it focused on the recent chat."),
            ("n8n suggests connecting the Chat Trigger and the agent to the same "
             "memory. One heads-up: Simple Memory isn't for queue mode."))),
        Scene("", "subscribe", "Still with me?", said=(
            "If you're building your first agent with me, subscribe.",)),
        Scene("", "pause", "Pause and try this", items=(
            "Add the agent, a model and the Calculator",
            "Add Simple Memory under it",
        ), said=(
            "Pause the video and add the agent, your chat model and the Calculator,",
            "then hang Simple Memory under it. Resume when they're all connected.")),
        Scene("Test it", "steps", "Test it in the chat", items=(
            "Open the chat, ask a sum",
            "Then ask a follow-up: 'and times two?'",
            "Error? + Chat Model is missing",
        ), said=(
            ("Open the chat and ask it a multiplication. [excited] Watch it call the "
             "Calculator, and answer."),
            ("Then ask a follow-up, like: and times two? If memory works, it knows "
             "what you mean."
             " Without memory, it would have no idea."),
            ("[sighing] Seeing a Chat Model sub-node must be connected? Open the agent "
             "and click the plus for Chat Model."))),
        Scene("Keep it safe", "bullets", "What it may and may not do", items=(
            "Only the tools it needs",
            "Max Iterations: a limit, default 10",
            "Approval before a risky tool runs",
        ), said=(
            ("Before you give an agent real jobs, keep it on a short leash. Give it "
             "only the tools it needs."),
            ("Max Iterations caps how many times the model runs per question. The "
             "default is ten."),
            ("And for a tool that sends or changes things, n8n can ask a person to "
             "approve first. Denied means it doesn't run."
             " The review can even happen in a different channel from the chat."))),
        Scene("", "quiz", "A tool sends emails. How do you stay in control?", items=(
            "Trust the model", "Require approval for that tool", "Remove memory",
        ), answer=1, caption="Human review: approve, or it doesn't run.", said=(
            ("Last quiz. One tool sends emails to customers. How do you stay in "
             "control? Comment."),
            "B. Require approval for that tool. If you deny it, nothing is sent.")),
        lab_scene("Lab: your first agent", "Lab: your first agent", LAB_FILE,
                  do=f"Import {LAB_FILE} · add AI Agent, model, Calculator",
                  expect="Intermediate steps show a Calculator call",
                  stuck="No prompt specified? Prompt: Define below",
                  challenge="Add Simple Memory, then ask a follow-up",
                  said=(
                      ("Your lab. Download the starter file from the description, and "
                       "import it. After Question, add an AI Agent, your chat model, and "
                       "the Calculator."),
                      ("Turn on Return Intermediate Steps in the agent's options, and "
                       "run it. Expect a Calculator call in the output."),
                      ("Stuck on no prompt specified? Set Prompt to Define below, and "
                       "drag in the chat input field."),
                      ("Your challenge: add Simple Memory, and make it answer a "
                       "follow-up question."))),
        Scene("Recap", "checklist", "Recap", items=(
            "Chat Trigger → AI Agent",
            "A chat model, and at least one tool",
            "Simple Memory for follow-ups",
            "Limits and approvals keep it safe",
        ), said=(
            "Recap. A Chat Trigger sends messages to the AI Agent node.",
            "The agent needs a chat model, and at least one tool it can choose.",
            "Simple Memory lets it follow a conversation.",
            ("[happy] And Max Iterations and approvals keep it in check. You've built "
             "your first agent."))),
    ),
)
