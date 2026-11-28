"""Approved by Miro 2026-11-24 (he authorised the review); facts re-read that day.

Study note: docs/academy-references/n8n_09.md

n8n #9: sub-workflows: build a step once, call it from any workflow.

Written 2026-11-24 as a draft; a new entry (the plan ran out at #8), added
under docs/academy-script-writer.md section 2b: the series promise is "n8n
Workflows from Zero", and #3 to #8 never teach reuse. No live demo: the
picture is steps, bullets, two quizzes and a lab.

Every product fact was read on the official n8n docs on 2026-11-24:

  docs.n8n.io/build/flow-logic/convert-to-sub-workflows.md
      ("refactor your workflows into reusable parts"; select the nodes,
      right-click the canvas background, "Convert to sub-workflow"; the
      selection must be continuous, hold no trigger nodes, have only one
      node receiving connections from outside (not a Merge) and only one
      node sending connections outside (not an If), and include every
      node between them; expressions that reference other nodes become
      parameters of the Execute Workflow Trigger node; input and output
      accept all types by default, so you set the types yourself; "available
      from n8n 1.97.0"; the new workflow uses v1 execution ordering)
  docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.executeworkflow.md
      (Source: Database, Local File, Parameter, URL; the workflow ID is the
      string after /workflow/ in its URL; Workflow Inputs appear when you
      pick it from the database list; Mode: Run once with all items, Run
      once for each item; Wait for Sub-Workflow Completion; the
      sub-workflow's last node sends its data back to the calling node; a
      sub-workflow with errors can't be triggered by the parent)
  docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.executeworkflowtrigger.md
      (starts a workflow when another workflow calls it; Input data mode:
      Define using fields below, Define using JSON example, Accept all
      data; with Accept all data the sub-workflow "must handle any input
      inconsistencies or missing values")

A lab may hold only Manual Trigger, Webhook, Schedule Trigger, HTTP Request
or Edit Fields, so the lab file holds a plain chain of Edit Fields nodes
that the viewer converts into a sub-workflow by hand: the exercise.
"""
from __future__ import annotations

from ..tutorial_series import (
    Episode,
    Scene,
    lab_scene,
    payoff_scene,
    plan,
    scripted,
)

# Checked 2026-11-24 against the docs (not the editor): the trigger is "Execute
# Sub-workflow Trigger"; conversion is from n8n 1.97.0 and adds a Return (Edit
# Fields) node; the lab selection (Name + Greeting) has one node taking input from
# outside and one sending output out, which is the rule on the convert page.

PLANNED = plan(
    9, 3, "n8n-sub-workflows",
    "n8n Sub-Workflows: Build a Step Once and Reuse It in Any Workflow",
    "Build once,\nreuse",
    ("How to split a workflow into reusable parts: the Execute Sub-workflow "
     "Trigger, the Execute Workflow node, what comes back, and the shortcut that "
     "converts nodes you already have."),
    ("n8n sub workflow", "n8n execute workflow", "n8n execute sub-workflow trigger",
     "n8n reusable workflow"),
    ("#n8n", "#aiautomation", "#aiagents"),
    ("The payoff: one step, called from a workflow, in a few clicks",
     "What you need: n8n, the lab file",
     "The child: Execute Sub-workflow Trigger and its input modes",
     "The parent: the Execute Workflow node, source and inputs",
     "Mode, waiting, and what the last node sends back",
     "The shortcut: Convert to sub-workflow, and its rules",
     "Lab: convert two nodes into a sub-workflow",
     "Recap"),
)

LAB_JSON = """{
  "name": "ATLAS Academy lab: convert to a sub-workflow",
  "nodes": [
    { "name": "Start", "type": "n8n-nodes-base.manualTrigger",
      "typeVersion": 1, "position": [0, 0], "parameters": {} },
    { "name": "Name", "type": "n8n-nodes-base.set",
      "typeVersion": 3.4, "position": [240, 0],
      "parameters": { "assignments": { "assignments": [
          { "id": "lab9-name", "name": "name", "value": "Ada",
            "type": "string" } ] },
        "options": {} } },
    { "name": "Greeting", "type": "n8n-nodes-base.set",
      "typeVersion": 3.4, "position": [480, 0],
      "parameters": { "assignments": { "assignments": [
          { "id": "lab9-greeting", "name": "greeting",
            "value": "=Hello, {{ $json.name }}", "type": "string" } ] },
        "options": {} } },
    { "name": "Message", "type": "n8n-nodes-base.set",
      "typeVersion": 3.4, "position": [720, 0],
      "parameters": { "assignments": { "assignments": [
          { "id": "lab9-message", "name": "message",
            "value": "={{ $json.greeting }}! Welcome to the lab.",
            "type": "string" } ] },
        "options": {} } }
  ],
  "connections": {
    "Start": { "main": [[{ "node": "Name", "type": "main", "index": 0 }]] },
    "Name": { "main": [[{ "node": "Greeting", "type": "main", "index": 0 }]] },
    "Greeting": { "main": [[{ "node": "Message", "type": "main", "index": 0 }]] }
  }
}"""

LAB_FILE = "n8n-sub-workflow-lab.json"
LAB_FILES: dict[str, str] = {LAB_FILE: LAB_JSON + "\n"}

PICTURE = (
    "Build a step\nonce",
    "Call it\nfrom anywhere",
    "The answer\ncomes back",
)

EPISODE: Episode = scripted(
    PLANNED,
    title_hook="n8n Sub-Workflows",
    title_benefit="Reuse Any Step",
    result="A reusable step",
    links=(("Convert to sub-workflow",
            "https://docs.n8n.io/build/flow-logic/convert-to-sub-workflows/"),
           ("Execute Workflow node",
            ("https://docs.n8n.io/integrations/builtin/core-nodes/"
             "n8n-nodes-base.executeworkflow/")),
           ("Execute Workflow Trigger node",
            ("https://docs.n8n.io/integrations/builtin/core-nodes/"
             "n8n-nodes-base.executeworkflowtrigger/"))),
    ask="Which step do you keep copying between workflows? Tell me below.",
    topic_said=("Today: sub-workflows. How to build a step once, and call it from any "
                "other workflow."),
    topic_shown="Sub-workflows: build once, reuse",
    payoff=payoff_scene(
        ("By the end, you'll have turned two nodes into a small workflow of their "
         "own, ready to be called from anywhere."),
        "Main workflow → Execute Workflow → Hello, Ada",
        items=PICTURE,
        caption="one step, many workflows"),
    hook=Scene("What we'll build", "hook", "A step you reuse", items=PICTURE,
               code="Execute Sub-workflow Trigger → your steps\nExecute Workflow → calls it",
               caption="one step, many workflows", said=(
                   ("Copying the same nodes into workflow after workflow? [curious] "
                    "There's a better way."),
                   ("You need n8n, and nothing else. There's a small lab file at the "
                    "end, with the nodes to convert."))),
    needs=("n8n, Cloud or self-hosted", "the free lab file"),
    scenes=(
        Scene("Why sub-workflows", "bullets", "Why sub-workflows?", items=(
            "A sub-workflow is a workflow another one calls",
            "Reuse: build once, call from many",
            "Smaller canvas, easier to read",
        ), said=(
            ("A sub-workflow is just a workflow that another workflow calls. The "
             "caller is the parent, and the one it calls is the child."),
            ("The docs call it refactoring into reusable parts. Build a step once, "
             "and call it from as many workflows as you like."),
            "And as a bonus, your main canvas gets smaller and easier to read.")),
        Scene("The child", "steps", "Build the child", items=(
            "Start with Execute Sub-workflow Trigger",
            "Input mode: fields, JSON example, or all data",
            "Add the steps you want to reuse",
        ), said=(
            ("Make a new workflow, and start it with the Execute Sub-workflow "
             "Trigger. It starts the workflow when another one calls it."),
            ("Choose how inputs arrive. Define them as fields, give a JSON example, "
             "or accept all data. [emphasis] With all data, your steps must cope with "
             "missing values."),
            ("Then add the steps you want to reuse. Whatever the last node sends is "
             "the answer."))),
        Scene("", "quiz", "Which node starts a sub-workflow?", items=(
            "Webhook", "Execute Sub-workflow Trigger", "Schedule Trigger",
        ), answer=1, caption="It starts when another workflow calls it.", said=(
            ("Quick quiz. Which trigger starts a workflow when another one calls it? "
             "Comment."),
            "B. The Execute Sub-workflow Trigger. It waits to be called.")),
        Scene("The parent", "steps", "Call it from the parent", items=(
            "Add an Execute Workflow node",
            "Source: Database, and pick the child",
            "Fill in its inputs",
        ), said=(
            ("In the parent, add an Execute Workflow node. It's the one that makes "
             "the call."),
            ("Set Source to Database, and pick the child from the list. You can also "
             "paste its ID. That's the string after slash workflow, in its address."),
            ("Pick the child, and its inputs appear, ready to fill in or map. They "
             "don't, if the child accepts all data."))),
        Scene("", "subscribe", "Reusing steps already?", said=(
            "If this is saving you copy and paste, subscribe.",)),
        Scene("Mode and waiting", "bullets", "Mode and waiting", items=(
            "Run once with all items, or once for each item",
            "Wait for Sub-Workflow Completion",
            "The last node's data comes back",
        ), said=(
            ("Mode decides the calls. Once with all items sends everything together. "
             "Once for each item calls the child per item."),
            ("Wait for Sub-Workflow Completion makes the parent pause until the child "
             "is done. Turn it off, and the parent moves on."),
            ("[confident] And what comes back is the data from the child's last node. "
             "Keep field names identical on both sides, capital letters included."))),
        Scene("", "pause", "Pause and try this", items=(
            "Make a child with a trigger and one node",
            "Call it with an Execute Workflow node",
        ), said=(
            ("Pause the video, and make a child workflow with the trigger and one Edit "
             "Fields node."),
            ("Then call it from a second workflow, and look at what comes back. Resume "
             "when you have."))),
        Scene("The shortcut", "steps", "The shortcut: convert", items=(
            "Select a run of nodes",
            "Right-click the canvas: Convert to sub-workflow",
            "n8n builds the child and the call",
        ), said=(
            ("Already built it inline? [excited] You don't have to rebuild. Select "
             "the nodes you want to move."),
            "Right-click the empty canvas, and choose Convert to sub-workflow.",
            ("n8n makes the new workflow, and leaves a call in its place. References "
             "to other nodes become inputs of the trigger."))),
        Scene("The rules", "bullets", "The rules for converting", items=(
            "Continuous selection, no trigger",
            "One node in from outside, one node out",
            "Set the input and output types yourself",
        ), said=(
            ("The selection has to be continuous, and it can't include a trigger node."),
            ("Only one node may receive from outside, and only one may send out. The "
             "input can't be a Merge, and the output can't be an If."),
            ("Types are open by default. Set them yourself in the new trigger, and in "
             "the Return node. One more: a child with errors can't be triggered."))),
        Scene("", "quiz", "Can the selection include the trigger?", items=(
            "Yes", "No, no trigger nodes", "Only a Webhook",
        ), answer=1, caption="A selection can't contain trigger nodes.", said=(
            ("Last quiz. You want to convert a selection. Can it include the trigger? "
             "Comment."),
            "B. No. Leave the trigger out, and convert the nodes after it.")),
        lab_scene("Lab: convert to a sub-workflow", "Lab: convert nodes", LAB_FILE,
                  do=f"Import {LAB_FILE} · select Name + Greeting · Convert",
                  expect="Run it: 'Hello, Ada! Welcome to the lab.'",
                  stuck="No menu entry? Select only continuous nodes, no trigger",
                  challenge="Call the new child from a second workflow",
                  said=(
                      ("Your lab. Download the lab file from the description, and "
                       "import it. Select the Name and Greeting nodes, and convert "
                       "them to a sub-workflow."),
                      ("Run the main workflow. Expect the message to read: Hello, Ada, "
                       "welcome to the lab. The same result, now from a child."),
                      ("Stuck with no menu entry? Select only connected nodes, and "
                       "leave out the Start trigger."),
                      ("Your challenge: call the new child from a second workflow, "
                       "with an Execute Workflow node."))),
        Scene("Recap", "checklist", "Recap", items=(
            "A child starts with Execute Sub-workflow Trigger",
            "The parent calls it with Execute Workflow",
            "The last node's data comes back",
            "Convert to sub-workflow reuses what you built",
        ), said=(
            "Recap. A child workflow starts with the Execute Sub-workflow Trigger.",
            "The parent calls it with the Execute Workflow node.",
            "What comes back is the data from the child's last node.",
            ("[happy] And Convert to sub-workflow turns nodes you already have into "
             "a reusable step."))),
    ),
)
