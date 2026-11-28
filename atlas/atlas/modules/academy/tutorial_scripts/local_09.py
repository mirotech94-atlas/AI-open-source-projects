"""Local #9: Hermes Agent and n8n, connected through MCP or plain HTTP.

Study note: docs/academy-references/local_09.md

Approved by Miro 2026-11-24 (he authorised the review); facts re-read that day.

Written 2026-11-24. No live demo: the picture is a config sketch, steps,
bullets, a diagram, two quizzes and a pause. The script names the official
n8n entry in Hermes' MCP catalog first, then two routes you build yourself.

Read on 2026-11-24 (New York time):

  docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-langchain.mcptrigger/
      (MCP Server Trigger: n8n acts as an MCP server; exposes a URL; connects
      only to tool nodes; clients list and call tools; SSE and streamable
      HTTP, no stdio; auth None, n8n User Auth (OAuth2), Bearer, Header;
      test URL vs production URL (production when the workflow is
      published); random path by default)
  docs.n8n.io/integrations/builtin/cluster-nodes/sub-nodes/
      n8n-nodes-langchain.toolmcp/
      (MCP Client Tool: uses tools from an external MCP server; parameter
      "SSE Endpoint"; auth Bearer, generic header, multiple headers, OAuth2,
      None)
  docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.httprequest/
      (HTTP Request: GET POST PUT PATCH DELETE and more; predefined or
      generic credentials; "Import cURL")
  hermes-agent.nousresearch.com/docs/user-guide/features/mcp
      (through sibling local_06: mcp_servers in ~/.hermes/config.yaml, url
      and headers for remote servers, tools include/exclude)

  (also read the same day: the "n8n-official" catalog entry connects Hermes to an
      n8n Cloud or self-hosted instance over HTTP with browser OAuth; an owner or
      admin enables instance-level MCP in n8n settings; the server URL ends in
      /mcp-server/http; hermes mcp install n8n-official, then hermes mcp configure
      n8n-official; /reload-mcp; remote entries take url and headers, Streamable
      HTTP by default; hermes mcp test shows what a remote server returned)
  docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/
      (the Webhook node is a trigger that starts a workflow from an HTTP request;
      GET, POST and more; test and production URLs; production when published)

Not claimed: that Hermes sends webhook calls on its own, and which header the n8n
Bearer auth reads (the script only says to send the token in a header).
"""
from __future__ import annotations

from ..tutorial_series import (
    LOCAL_PLANNED,
    Episode,
    Scene,
    payoff_scene,
    scripted,
)

CONFIG = """mcp_servers:
  n8n:
    url: "https://n8n.example.com/mcp/<your-path>"
    headers:
      Authorization: "Bearer <token from n8n>"
    tools:
      include: [send_summary]"""

PICTURE = (
    "n8n MCP Server\nTrigger node",
    "Hermes mcp_servers\nentry",
    "One tool,\nnothing more",
)

EPISODE: Episode = scripted(
    next(e for e in LOCAL_PLANNED if e.number == 9),
    title="Connect Hermes Agent and n8n with MCP or HTTP",
    title_hook="Hermes Agent and n8n",
    title_benefit="MCP or HTTP",
    result="Hermes calls n8n",
    tags=("hermes agent n8n", "n8n mcp server trigger", "n8n http request",
          "advanced", "ATLAS Academy"),
    links=(("n8n: MCP Server Trigger",
            ("https://docs.n8n.io/integrations/builtin/core-nodes/"
             "n8n-nodes-langchain.mcptrigger/")),
           ("n8n: HTTP Request",
            ("https://docs.n8n.io/integrations/builtin/core-nodes/"
             "n8n-nodes-base.httprequest/")),
           ("Hermes Agent: MCP",
            "https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp")),
    ask="What would you hand from Hermes to an n8n workflow first? Tell me below.",
    topic_said=("Today: Hermes Agent and n8n. Hermes has an official n8n entry, "
                "and we'll also build the link ourselves."),
    topic_shown="Hermes and n8n, connected",
    payoff=payoff_scene(
        ("By the end, you'll know two ways to join them, and you'll have Hermes "
         "calling one n8n tool and nothing else."),
        "n8n MCP Server Trigger · Hermes mcp_servers · include: one tool",
        items=PICTURE,
        caption="two ways in"),
    hook=Scene("What you'll learn", "hook", "Hermes plus n8n", items=PICTURE,
               code=CONFIG, caption="two ways in", said=(
                   ("Hermes thinks. n8n does the routine work. [curious] So how do they "
                    "talk?"),
                   ("You need Hermes Agent, and an n8n you can reach. And a straight "
                    "answer first: Hermes already lists an official n8n entry."))),
    needs=("Hermes Agent, installed", "An n8n instance you control"),
    scenes=(
        Scene("The official entry", "bullets", "The quick route first", items=(
            "Hermes lists an n8n-official MCP entry",
            "An n8n admin turns on instance-level MCP",
            "hermes mcp install n8n-official, then sign in",
        ), said=(
            ("Hermes' docs list an MCP catalog entry called n8n dash official. It "
             "connects straight to your n8n over HTTP, with a browser sign-in."),
            ("An owner or admin turns on instance-level MCP in n8n's settings. You "
             "copy the server URL it shows."),
            ("Then you run hermes mcp install n8n dash official, and finish the "
             "sign-in. That's the quick route. Next, a route you control more."))),
        Scene("Option one: MCP", "steps", "Route one: MCP", items=(
            "n8n: add an MCP Server Trigger node",
            "Attach the tools you want to expose",
            "Hermes: add its URL to mcp_servers",
        ), said=(
            ("In n8n, add the MCP Server Trigger node. It gives you a URL that MCP "
             "clients connect to."),
            ("It only connects to tool nodes. So you pick exactly which workflows "
             "or tools Hermes can call."),
            ("Then in Hermes, you add that URL under mcp servers, like any remote "
             "server from the last episodes."))),
        Scene("The config", "code", "Hermes config for n8n", code=CONFIG,
              marks=((0, 3), (3, 5), (5, 7)),
              caption="placeholders only", said=(
                  ("The URL is the one n8n shows you. Use the production one once "
                   "your workflow is published."),
                  ("Protect it with a bearer token, and send it as a header. Never "
                   "paste the real token on screen."),
                  ("And include lists one tool. Hermes sees that tool and nothing else "
                   "from n8n."))),
        Scene("", "quiz", "What does the MCP Server Trigger connect to?", items=(
            "Only tool nodes", "Any node in the workflow",
        ), answer=0, caption="It connects only to tool nodes.", said=(
            ("Quick quiz. The MCP Server Trigger node connects to what? Comment your "
             "answer."),
            "A. Only tool nodes. That's what keeps the list short.")),
        Scene("Option two: HTTP", "steps", "Route two: plain HTTP", items=(
            "n8n's HTTP Request node calls any web API",
            "Import a curl command to fill it in",
            "Pick an auth type and keep it in credentials",
        ), said=(
            ("The other route is plain HTTP. n8n's HTTP Request node can call any "
             "service with a web API, using methods like GET and POST."),
            ("You can paste a curl command and import it, and n8n fills in the "
             "node. Handy for testing."),
            ("It has several auth types. Pick one, and store the secret in n8n's "
             "credentials, not in the workflow."))),
        Scene("", "subscribe", "Running AI at home?", said=(
            "If you're building your own local agent, subscribe for the rest.",)),
        Scene("Which route", "diagram", "Choose a route", items=(
            "Hermes calls n8n: MCP",
            "n8n calls a web API: HTTP Request",
            "Anything sends a web request: Webhook node",
        ), said=(
            ("If Hermes should use n8n as a toolbox, use MCP. Hermes finds the tools "
             "by itself."),
            ("If n8n should call out to something, use the HTTP Request node. It's the "
             "right tool when the other side is just a web API."),
            ("And if anything can send one web request, n8n's Webhook node can "
             "start a workflow from it."))),
        Scene("A small automation", "bullets", "One tiny workflow", items=(
            "n8n: a workflow that sends a summary",
            "Exposed as one tool",
            "Hermes calls it when you ask",
        ), said=(
            ("Here's a small plan. Build one n8n workflow that sends a message "
             "with a summary in it."),
            ("Expose that workflow as a single tool, and put the tool name on "
             "Hermes' include line. Start small, and check it works before you add more."),
            ("Now you can ask Hermes to send the summary, and n8n does the "
             "sending. [happy] Each does what it's good at."))),
        Scene("", "pause", "Pause and try this", items=(
            "Add the MCP Server Trigger in n8n",
            "Add the entry to Hermes, then test it",
        ), said=(
            ("Pause the video and add the MCP Server Trigger node to a test workflow. "
             "Attach one harmless tool."),
            ("Add the URL to Hermes with an include line, then run hermes mcp test. "
             "Resume when it lists your tool."))),
        Scene("When it fails", "steps", "If Hermes can't see it", items=(
            "Test URL or production URL?",
            "Is the workflow published?",
            "hermes mcp test shows what answered",
        ), said=(
            ("[sighing] Hermes sees no tool? Check which URL you used. n8n has a "
             "test one and a production one, and they're not the same."),
            ("The production URL only works once the workflow is published. The test URL "
             "only answers while you're listening in the editor."),
            ("And hermes mcp test shows what the server really answered. Read that "
             "before you change anything."))),
        Scene("", "quiz", "When does the production URL work?", items=(
            "Only after you publish the workflow", "Always",
        ), answer=0, caption="Production URL: after you publish.", said=(
            ("Last quiz. When does n8n's production URL work? Comment."),
            "A. Once the workflow is published. Before that, only the test URL.")),
        Scene("Recap", "checklist", "Recap", items=(
            "Quick route: the n8n-official catalog entry",
            "MCP: n8n server, Hermes client",
            "HTTP: the HTTP Request node, with curl import",
            "Expose one tool, not everything",
        ), said=(
            "Recap. Hermes lists an official n8n entry in its MCP catalog.",
            "So use MCP: n8n as the server, and Hermes as the client.",
            "Or use HTTP, with the HTTP Request node and credentials.",
            ("[confident] Whichever you pick, expose one tool, then widen it later."))),
    ),
)
