"""Approved by Miro 2026-11-24 (he authorised the review); facts re-read that day.

Study note: docs/academy-references/n8n_08.md

n8n #8: the AI agent lab: a tool call, memory and an approval step.

Written 2026-11-24 as a draft. No live demo: the picture is steps, bullets,
two quizzes and a lab. It reuses n8n #5's agent facts (read 2026-10-17, see
tutorial_scripts/n8n_05.py) and re-read these official pages on 2026-11-24:

  docs.n8n.io/integrations/builtin/cluster-nodes/root-nodes/n8n-nodes-langchain.agent/tools-agent.md
      (Max Iterations "Defaults to 10"; Return Intermediate Steps: include
      "intermediate steps the agent took in the final output"; System
      Message, sent before the conversation starts)
  docs.n8n.io/build/integrate-ai/ai-examples/human-in-the-loop-for-tools.md
      (click the Tools connector on an AI Agent node; the Human review
      section of the Tools Panel; choose an approval channel, Chat among
      them; attach the tools that need approval; Approve: "The tool
      executes with the input specified by the AI"; Deny: "The action is
      canceled and doesn't run" and the AI is told; describe the review in
      the system prompt)

Also read on 2026-11-24: the agent's common-issues page ("A Chat Model
sub-node must be connected": click + Chat Model; "No prompt specified": change
Prompt from Connected Chat Trigger Node to Define below); the agent page ("You
must connect at least one tool sub-node"); the Simple Memory page (persists
chat history; Context Window Length: number of previous interactions); the Chat
Trigger page (with Load Previous Session set to From Memory, connect the
trigger and the agent to a memory sub-node, ideally the same one).

Not claimed: what the output of Return Intermediate Steps looks like, or the
exact chat wording of the approval step.

A lab may hold only credential-free nodes (tests/test_academy_labs.py), and
an agent needs a chat model credential, so the lab file is a starter: a
Manual Trigger and Edit Fields (Set v3.4) holding the question. The viewer
adds the agent, model, Calculator, memory and the approval by hand, which
are the three exercises.
"""
from __future__ import annotations

from ..tutorial_series import N8N_PLANNED as _PLANNED
from ..tutorial_series import (
    Episode,
    Scene,
    lab_scene,
    payoff_scene,
    scripted,
)

LAB_JSON = """{
  "name": "ATLAS Academy lab: agent lab (starter)",
  "nodes": [
    { "name": "Start", "type": "n8n-nodes-base.manualTrigger",
      "typeVersion": 1, "position": [0, 0], "parameters": {} },
    { "name": "Question", "type": "n8n-nodes-base.set",
      "typeVersion": 3.4, "position": [240, 0],
      "parameters": { "assignments": { "assignments": [
          { "id": "lab8-question", "name": "chatInput",
            "value": "What is 48 times 36? Use your calculator.",
            "type": "string" } ] },
        "options": {} } }
  ],
  "connections": {
    "Start": { "main": [[{ "node": "Question", "type": "main", "index": 0 }]] }
  }
}"""

LAB_FILE = "n8n-agent-lab-starter.json"
LAB_FILES: dict[str, str] = {LAB_FILE: LAB_JSON + "\n"}

PICTURE = (
    "A question\ngoes in",
    "A tool call\nin the log",
    "An approval\nbefore it acts",
)

EPISODE: Episode = scripted(
    next(e for e in _PLANNED if e.number == 8),
    title_hook="n8n AI Agent Lab",
    title_benefit="Test Your Agent",
    result="A tested agent",
    links=(("AI Agent node: Tools Agent options",
            ("https://docs.n8n.io/integrations/builtin/cluster-nodes/root-nodes/"
             "n8n-nodes-langchain.agent/tools-agent/")),
           ("Human review for tool calls",
            ("https://docs.n8n.io/build/integrate-ai/ai-examples/"
             "human-in-the-loop-for-tools/")),
           ("AI Agent common issues",
            ("https://docs.n8n.io/integrations/builtin/cluster-nodes/root-nodes/"
             "n8n-nodes-langchain.agent/common-issues/"))),
    ask="What would your agent never be allowed to do without asking? Tell me below.",
    topic_said=("Today: a hands-on lab. Three short exercises with an n8n AI agent: a "
                "tool call, memory, and an approval."),
    topic_shown="An AI agent lab, hands-on",
    payoff=payoff_scene(
        ("By the end, you'll have watched your agent call a tool, remember a name, and "
         "stop and wait for your yes before it acts."),
        "You: 48 × 36? → Calculator call → waits for Approve",
        items=PICTURE,
        caption="a tool, a memory, an approval"),
    hook=Scene("What we'll do", "hook", "Three exercises", items=PICTURE,
               code="1 · tool call   2 · memory   3 · approval",
               caption="a tool, a memory, an approval", said=(
                   ("You've built an agent. [curious] But do you trust it? Today we "
                    "test it, one exercise at a time."),
                   ("You need n8n and a chat model credential. And there's a starter "
                    "lab file to import, so we begin in seconds."))),
    needs=("n8n, Cloud or self-hosted", "one chat model credential",
           "the free starter lab file"),
    scenes=(
        Scene("Three exercises", "bullets", "Three exercises", items=(
            "1. Make it call a tool",
            "2. Make it remember",
            "3. Make it ask first",
        ), said=(
            ("This is a lab, so you do the work. Three exercises, and each one has a "
             "result to expect."),
            "First, we make the agent call a tool, and we look at the proof.",
            ("Then we give it a memory. And last, we make it ask a person before it "
             "acts."))),
        Scene("Exercise 1: a tool call", "steps", "Exercise 1: one tool call", items=(
            "Import the starter, add an AI Agent after Question",
            "Add your chat model and the Calculator",
            "Agent options: Return Intermediate Steps",
        ), said=(
            ("Import the starter file, and add an AI Agent after the Question node. "
             "Set its prompt to Define below, and use the chat input field."),
            "Under the agent, add your chat model, and the Calculator tool.",
            ("Then in the agent's options, add Return Intermediate Steps and turn it "
             "on. [emphasis] Now run it. The output includes the steps the agent "
             "took, so you can look for the tool you added."))),
        Scene("", "quiz", "How do you see which tool the agent called?", items=(
            "Guess from the answer", "Return Intermediate Steps", "Max Iterations",
        ), answer=1, caption="Intermediate steps show what the agent did.", said=(
            "Quick quiz. How do you see which tool the agent called? Comment.",
            ("B. Return Intermediate Steps. It adds the steps to the final output, so "
             "you don't have to guess."))),
        Scene("Exercise 2: memory", "steps", "Exercise 2: it remembers", items=(
            "Chat Trigger → AI Agent",
            "Add Simple Memory to the agent",
            "Say your name, then ask for it",
        ), said=(
            ("Memory needs a conversation. Swap the start for a Chat Trigger, and "
             "connect it to the agent."),
            ("Add Simple Memory under the agent, and connect the same memory to the "
             "Chat Trigger, as n8n recommends. It holds the chat history, and the "
             "window length says how many earlier turns it reads."),
            ("Open the chat. Say: my name is Sam. Then ask: what's my name? "
             "[excited] With the memory connected, the second answer can use the "
             "first message. If it can't, check the memory connection."))),
        Scene("", "subscribe", "Doing it with me?", said=(
            "If you're running these exercises along with me, subscribe.",)),
        Scene("Exercise 3: approval", "steps", "Exercise 3: ask before acting", items=(
            "Click the agent's Tools connector",
            "Human review: choose Chat",
            "Attach the Calculator to it",
            "Run it, then Deny",
        ), said=(
            "Click the Tools connector on the agent. That opens the Tools Panel.",
            "Find the Human review section, and choose Chat as the approval channel.",
            ("Attach the Calculator to the review step, so every call needs a yes. "
             "Also tell the agent about it in the system message."),
            ("Run it, and you're asked to approve. Approve, and the tool runs. [confident] "
             "Deny, and the action is canceled, and the agent is told."))),
        Scene("", "pause", "Pause and try this", items=(
            "Run exercise 1 and read the steps",
            "Run exercise 3 and press Deny",
        ), said=(
            "Pause the video, and run exercise one. Read the steps in the output.",
            ("Then run the approval, press Deny, and see that nothing ran. Resume when "
             "you have."))),
        Scene("Common errors", "bullets", "When it goes wrong", items=(
            "'Chat Model must be connected': + Chat Model",
            "'No prompt specified': Define below",
            "Loops? Max Iterations, default 10",
        ), said=(
            ("[sighing] A Chat Model sub-node must be connected? Click the plus for "
             "Chat Model under the agent, and pick one."),
            ("No prompt specified? Change the agent's prompt to Define below, and "
             "give it the chat input."),
            ("And if an agent seems to go round in circles, check Max Iterations. It "
             "caps how many times the model runs, and the default is ten."))),
        Scene("", "quiz", "You press Deny on a tool call. What happens?", items=(
            "The tool runs anyway", "It's canceled, and the agent is told",
            "n8n shuts down",
        ), answer=1, caption="Deny cancels the action. It doesn't run.", said=(
            "Last quiz. You press Deny on a tool call. What happens? Comment.",
            ("B. The action is canceled, and the AI is told it was turned down."))),
        lab_scene("Lab: the agent lab", "Lab: the agent lab", LAB_FILE,
                  do=f"Import {LAB_FILE} · add agent, model, Calculator",
                  expect="Steps list a Calculator call · Deny stops it",
                  stuck="Nothing to approve? Attach the tool to Human review",
                  challenge="Add a second tool. What must it never do?",
                  said=(
                      ("Your lab. Download the starter file from the description, and "
                       "import it. Add the agent, your chat model and the Calculator."),
                      ("Expect a Calculator call in the steps. Then add the approval, "
                       "and expect the run to stop and wait for you."),
                      ("Stuck with nothing to approve? Make sure the tool is attached "
                       "to the human review step, not straight to the agent."),
                      ("Your challenge: add a second tool, and decide what it must never "
                       "do without asking."))),
        Scene("Recap", "checklist", "Recap", items=(
            "Intermediate steps show the tool call",
            "Simple Memory carries a conversation",
            "Human review: Approve runs, Deny cancels",
            "Max Iterations stops loops",
        ), said=(
            ("Recap. Turn on Return Intermediate Steps, and you can see which tool the "
             "agent called."),
            "Simple Memory lets the second message use the first.",
            "Human review puts a person in charge: Approve runs the tool, Deny cancels it.",
            ("[happy] And Max Iterations stops loops. Now you can test an agent, "
             "instead of just hoping."))),
    ),
)
