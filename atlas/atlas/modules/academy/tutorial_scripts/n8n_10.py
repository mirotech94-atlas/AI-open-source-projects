"""Approved by Miro 2026-11-24 (he authorised the review); facts re-read that day.

Study note: docs/academy-references/n8n_10.md

n8n #10: the Code node: the basics, and when not to reach for it.

Written 2026-11-24 as a draft; a new entry (the plan ran out at #8), added
under docs/academy-script-writer.md section 2b: after webhooks, APIs, agents
and sub-workflows, the next level-3 skill the series promise covers is the
step that does what no built-in node does. No live demo: the picture is
bullets, steps, one code sample, two quizzes and a lab.

Every product fact was read on the official n8n docs on 2026-11-24:

  docs.n8n.io/build/code-in-n8n.md
      (use no code first: core nodes, flow logic nodes such as If, Switch
      and Merge, and expressions; "Use the Code node to add JavaScript or
      Python to your workflow" when those aren't enough)
  docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.code.md
      (JavaScript, and Python; modes "Run Once for All Items" (the default,
      "executes once, regardless of how many input items there are") and
      "Run Once for Each Item"; "You can't access the file system or make
      HTTP requests", use Read/Write File From Disk and HTTP Request;
      self-hosted can import built-in and external npm modules once
      enabled; on Cloud only crypto and moment; native Python via task
      runners is stable as of n8n 2, Pyodide removed in 2.0; native Python
      takes bracket notation such as item["json"]["x"])
  docs.n8n.io/build/code-in-n8n/using-the-code-node.md
      (the two modes again; console.log writes to the browser console and
      helps debugging)
  docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.code/common-issues.md
      ("Code doesn't return items properly": return an array of objects
      with your data in a json key; "A 'json' property isn't an object";
      "Code doesn't return an object" when it returns nothing; code can't
      read credentials, use the HTTP Request node)

A lab may hold no Code node (tests/test_academy_labs.py node list), so the
lab file does the job with Edit Fields; the challenge is to redo it in a
Code node by hand. That is also the lesson: try built-in nodes first.
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

# Not taught: the $input helpers (the Code node page does not document them).
# console.log output goes to the browser console, per the Code node page.

PLANNED = plan(
    10, 3, "n8n-code-node",
    "n8n Code Node: JavaScript Basics, the Return Format and When Not to Use It",
    "Code node:\nlast resort",
    ("The n8n Code node for beginners: the two modes, the item format you must "
     "return, what it cannot do, and why built-in nodes and expressions come first."),
    ("n8n code node", "n8n javascript", "n8n run once for each item",
     "n8n code node return format"),
    ("#n8n", "#aiautomation", "#aiagents"),
    ("The payoff: a calculation first without code, then in a Code node",
     "What you need: n8n, the lab file",
     "Why built-in nodes and expressions come first",
     "The two modes: all items, or each item",
     "The return format: an array of objects with a json key",
     "What it can't do: files, HTTP, credentials, modules",
     "Lab: do it with Edit Fields, then redo it in code",
     "Recap"),
)

LAB_JSON = """{
  "name": "ATLAS Academy lab: code last",
  "nodes": [
    { "name": "Start", "type": "n8n-nodes-base.manualTrigger",
      "typeVersion": 1, "position": [0, 0], "parameters": {} },
    { "name": "Order", "type": "n8n-nodes-base.set",
      "typeVersion": 3.4, "position": [240, 0],
      "parameters": { "assignments": { "assignments": [
          { "id": "lab10-boxes", "name": "boxes", "value": 4,
            "type": "number" },
          { "id": "lab10-per-box", "name": "per_box", "value": 3,
            "type": "number" } ] },
        "options": {} } },
    { "name": "Total", "type": "n8n-nodes-base.set",
      "typeVersion": 3.4, "position": [480, 0],
      "parameters": { "assignments": { "assignments": [
          { "id": "lab10-total", "name": "total",
            "value": "={{ $json.boxes * $json.per_box }}", "type": "number" } ] },
        "options": {} } }
  ],
  "connections": {
    "Start": { "main": [[{ "node": "Order", "type": "main", "index": 0 }]] },
    "Order": { "main": [[{ "node": "Total", "type": "main", "index": 0 }]] }
  }
}"""

LAB_FILE = "n8n-code-last-lab.json"
LAB_FILES: dict[str, str] = {LAB_FILE: LAB_JSON + "\n"}

SAMPLE = """return [
  { json: { message: "Hello from the Code node" } }
];"""

PICTURE = (
    "Try a built-in node\nfirst",
    "Then a Code node\nwhen it's not enough",
    "Items out\nwith a json key",
)

EPISODE: Episode = scripted(
    PLANNED,
    title_hook="n8n Code Node",
    title_benefit="Code Only When Needed",
    result="Code, used wisely",
    links=(("Code node",
            "https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.code/"),
           ("Using the Code node",
            "https://docs.n8n.io/build/code-in-n8n/using-the-code-node/"),
           ("Code node common issues",
            ("https://docs.n8n.io/integrations/builtin/core-nodes/"
             "n8n-nodes-base.code/common-issues/")),
           ("Code in n8n", "https://docs.n8n.io/build/code-in-n8n/")),
    ask="What would you use a Code node for first? Tell me below.",
    topic_said=("Today: the Code node. How it works, the format it must return, and "
                "when you shouldn't use it at all."),
    topic_shown="The Code node, and when not to use it",
    payoff=payoff_scene(
        ("By the end, you'll do one small job two ways, without code and with it, and "
         "you'll know which to pick."),
        "boxes × per_box → total: 12",
        items=PICTURE,
        caption="no code first, code when needed"),
    hook=Scene("What we'll do", "hook", "Code, but last", items=PICTURE,
               code=SAMPLE, caption="no code first, code when needed", said=(
                   ("Everyone wants to write code in n8n. [curious] But there's a "
                    "reason the docs say to leave it for later."),
                   ("You need n8n, and nothing more. The lab file at the end holds the "
                    "job to try both ways."))),
    needs=("n8n, Cloud or self-hosted", "the free lab file"),
    scenes=(
        Scene("Code comes last", "bullets", "Try these first", items=(
            "Core nodes: requests, webhooks, scheduling, files",
            "If, Switch and Merge for the logic",
            "Expressions to reshape data",
        ), said=(
            ("n8n's own advice is simple: start without code. The core nodes already "
             "cover requests, webhooks, scheduling and files."),
            "For choices and joins, there's If, Switch and Merge.",
            ("And expressions can reshape data right inside a node's fields. Only "
             "when those aren't enough, reach for the Code node."))),
        Scene("Add the Code node", "steps", "Add the Code node", items=(
            "Add a Code node",
            "Language: JavaScript, or Python",
            "Mode: all items, or each item",
        ), said=(
            "Add a Code node. It's where you add your own JavaScript or Python.",
            ("Pick your language. Python runs through task runners, and is stable "
             "from n8n two. JavaScript is the one most guides use."),
            ("The mode is next. Run Once for All Items is the default. Your code "
             "runs one time, however many items come in."))),
        Scene("Two modes", "bullets", "Two modes", items=(
            "All Items: runs once for the whole batch",
            "Each Item: runs once per item",
            "Pick the one that matches your job",
        ), said=(
            ("In all items mode, your code runs once and handles the whole batch "
             "together."),
            "In each item mode, the same code runs again for every item.",
            ("Counting or summing? All items. Changing each item the same way? Each "
             "item."))),
        Scene("", "quiz", "Which mode is the default?", items=(
            "Run Once for Each Item", "Run Once for All Items", "Neither",
        ), answer=1, caption="The default runs the code once.", said=(
            "Quick quiz. Which mode does a new Code node start in? Comment.",
            "B. Run Once for All Items. The code runs once, whatever the item count.")),
        Scene("The return format", "code", "What you must return", code=SAMPLE,
              caption="an array of objects, each with json", said=(
                  ("Now the rule that trips everyone. The node has to return a list of "
                   "items. Each item is an object, with your data inside a key called "
                   "json."),
                  ("[confident] Add console dot log while you build. It writes to your "
                   "browser console, which makes bugs much easier to find."))),
        Scene("", "subscribe", "Getting the hang of it?", said=(
            "If this is making code less scary, subscribe.",)),
        Scene("Common errors", "bullets", "The three classic errors", items=(
            "Code doesn't return items properly",
            "A json property isn't an object",
            "Code doesn't return an object",
        ), said=(
            ("[sighing] Code doesn't return items properly? Return an array of "
             "objects, with the data in a json key."),
            "A json property isn't an object? Your json is a list. Make it a plain object.",
            "Code doesn't return an object? Nothing came back. Return the items.")),
        Scene("", "pause", "Pause and try this", items=(
            "Add a Code node and return the sample",
            "Break it on purpose, read the error",
        ), said=(
            "Pause the video, and add a Code node that returns the sample.",
            ("Then break it on purpose, by returning nothing, and read the error. "
             "Resume when you have."))),
        Scene("Python notes", "bullets", "If you pick Python", items=(
            "Native Python: task runners, stable from n8n 2",
            "The old Pyodide option is gone in n8n 2",
            "Use brackets: item[\"json\"][\"x\"]",
        ), said=(
            ("If you pick Python, you get native Python, which runs through task "
             "runners. It's stable from n8n two."),
            "The older Pyodide version was removed in n8n two, so skip old guides about it.",
            ("And in native Python you write item, then json, then your field name, in "
             "square brackets. That's the form the docs show."))),
        Scene("What it can't do", "bullets", "What it can't do", items=(
            "No files, no HTTP requests",
            "Cloud: no outside modules",
            "No credentials: use HTTP Request",
        ), said=(
            ("The Code node can't touch the file system, and it can't make HTTP "
             "requests. Use Read Write File From Disk, and HTTP Request."),
            ("On n8n Cloud, you can't bring outside modules. Only crypto and moment "
             "are there. Self-hosted can enable more."),
            ("[emphasis] And code can't read your credentials. For a signed-in call, "
             "use the HTTP Request node."))),
        Scene("", "quiz", "You need to call an API. Which node?", items=(
            "Code node", "HTTP Request node", "Neither",
        ), answer=1, caption="Code can't make HTTP requests.", said=(
            "Last quiz. You need to call an API. Do you use a Code node? Comment.",
            "B. No. The HTTP Request node. A Code node can't make requests.")),
        lab_scene("Lab: code last", "Lab: code last", LAB_FILE,
                  do=f"Import {LAB_FILE} · Execute workflow",
                  expect="Total shows 12 · boxes × per_box, no code",
                  stuck="Total empty? Check the Order node's two numbers",
                  challenge="Redo it in a Code node, return json",
                  said=(
                      ("Your lab. Download the lab file from the description, and "
                       "import it. Run it."),
                      ("Expect a total of twelve. Four boxes times three each, done "
                       "with Edit Fields and one expression. No code."),
                      ("Stuck with an empty total? Open the Order node, and check "
                       "both numbers are set."),
                      ("Your challenge: do the same in a Code node. Return an item "
                       "with a json key holding the total."))),
        Scene("Recap", "checklist", "Recap", items=(
            "Built-in nodes and expressions first",
            "All items or each item",
            "Return items, with a json key",
            "No files, no HTTP, no credentials",
        ), said=(
            "Recap. Reach for built-in nodes and expressions before code.",
            "Choose all items, or each item, to match the job.",
            "Always return items, with your data inside a json key.",
            ("[happy] And remember what it can't do: files, requests and credentials. "
             "Code is a tool, not a habit."))),
    ),
)
