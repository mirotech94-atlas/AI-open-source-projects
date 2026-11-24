"""Project #3: writing our own MCP server and plugging it into n8n.

Written 2026-10-17 by the script writer (docs/academy-script-writer.md). No
live demo: no new recording can be made, so the picture is the server's
code, steps, a diagram, two quizzes and a pause.

Every product fact was read on the official pages on 2026-10-17:

  github.com/modelcontextprotocol/python-sdk README (raw main/README.md)
      (install: uv add "mcp[cli]"; "from mcp.server import MCPServer";
      the @mcp.tool() decorator; "uv run mcp dev server.py" opens the
      Inspector; "A URL means Streamable HTTP, the transport you deploy";
      PyPI lists mcp 2.3.0 as current)
  py.sdk.modelcontextprotocol.io/run/
      ("mcp.run() starts the server"; "With no argument, the transport is
      stdio"; mcp.run(transport="streamable-http"); "host / port: where to
      listen. Defaults 127.0.0.1 and 8000"; "streamable_http_path: where the
      MCP endpoint lives. Default /mcp"; "Transport options go to run(), not
      to MCPServer(...)")
  modelcontextprotocol.io/docs/develop/build-server.md
      ("For HTTP-based servers: Standard output logging is fine"; for stdio
      "Never write to stdout")
  modelcontextprotocol.io/docs/tools/inspector.md
      (npx @modelcontextprotocol/inspector, "requires Node 22.19.0 or
      newer" -- said only as "Node.js")
  docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-langchain.mcpclient.md
      ("You can use the MCP Client node to use MCP tools as regular steps in
      a workflow"; "If you want to use MCP tools as tools for an AI Agent,
      use the MCP Client Tool node instead"; Server Transport; "MCP Endpoint
      URL"; Tool "automatically fetched from the external MCP server";
      Authentication, None among the options)
  raw.githubusercontent.com/n8n-io/n8n/master/packages/%40n8n/nodes-langchain/nodes/mcp/shared/descriptions.ts
      (the docs page names the Server Transport field but not its choices;
      n8n's own source names them: "HTTP Streamable" and "Server Sent
      Events (Deprecated)")

The opening hours in the code are example data the viewer replaces, not a
fact about anyone's business.
"""
from __future__ import annotations

from ..tutorial_series import (
    PROJECT_PLANNED,
    Episode,
    Scene,
    payoff_scene,
    scripted,
)

SERVER_PY = '''from mcp.server import MCPServer

mcp = MCPServer("shop-info")

HOURS = {"monday": "9:00-17:00", "saturday": "10:00-14:00", "sunday": "closed"}


@mcp.tool()
def opening_hours(day: str) -> str:
    """Opening hours for one day of the week."""
    return HOURS.get(day.lower(), "Not listed: please ask a person.")


if __name__ == "__main__":
    mcp.run(transport="streamable-http")'''

PICTURE = (
    "Your server\none tool: opening hours",
    "MCP over HTTP\nlocalhost:8000/mcp",
    "n8n\ncalls it as a step",
)

EPISODE: Episode = scripted(
    next(e for e in PROJECT_PLANNED if e.number == 3),
    title_hook="to Write Your Own MCP Server for n8n",
    links=(("MCP Python SDK",
            "https://github.com/modelcontextprotocol/python-sdk"),
           ("Running a server (Python SDK docs)",
            "https://py.sdk.modelcontextprotocol.io/run/"),
           ("n8n MCP Client node",
            ("https://docs.n8n.io/integrations/builtin/core-nodes/"
             "n8n-nodes-langchain.mcpclient/"))),
    ask="What's the one tool your business assistant should have? Tell me below.",
    topic_said=("Today: our own MCP server, with one tool our assistant needs, called "
                "from an n8n workflow."),
    topic_shown="Your own MCP server, in n8n",
    payoff=payoff_scene(
        ("By the end, our n8n workflow asks our own little server for the shop's "
         "opening hours, and gets a real answer back."),
        'opening_hours("Saturday") → "10:00-14:00"',
        items=PICTURE,
        caption="your tool, your rules"),
    hook=Scene("What we'll build", "hook", "A tool of our own", items=PICTURE,
               code=SERVER_PY, caption="your tool, your rules", said=(
                   ("Ready-made MCP servers are great. But sometimes the tool you need "
                    "is about your business, and nobody has written it."),
                   ("[confident] So we'll write it. All you need is uv for Python, and "
                    "the n8n workspace from earlier in this project."))),
    needs=("uv (it installs Python for you)", "Node.js, for the MCP Inspector",
           "the project's n8n workspace"),
    scenes=(
        Scene("The one tool", "bullets", "The one tool we need", items=(
            "Customers ask: are you open Saturday?",
            "The answer lives in one place: our server",
            "One tool, one job: opening_hours",
        ), said=(
            ("Our assistant drafts replies to customers, and one question comes up "
             "again and again: are you open on Saturday?"),
            ("We don't want the model guessing that. We want the answer from one "
             "place we control."),
            ("So our server gets exactly one tool, called opening hours. You give it a "
             "day, it gives back that day's hours. And if a day isn't listed, it says "
             "ask a person, so it never makes one up."))),
        Scene("Set it up", "steps", "Set up the project", items=(
            "uv init shop-info",
            'uv add "mcp[cli]"',
            "Create server.py",
        ), said=(
            "Open a terminal and make a new project with uv init, called shop info.",
            ("Then add the official MCP Python SDK, with uv add mcp, and the cli extra "
             "in square brackets. That extra gives you the mcp command, for testing."),
            "Create a file called server.py. That's where the code goes.")),
        Scene("The server, line by line", "code", "server.py", code=SERVER_PY,
              marks=((0, 3), (4, 5), (7, 11), (13, 15)), caption="server.py", said=(
                  ("At the top, import MCPServer from the SDK, and give the server a "
                   "name: shop info."),
                  "Next, our data: a small table of days and hours. Put your own in.",
                  ("[emphasis] The decorator, mcp dot tool, turns a normal Python "
                   "function into a tool. Its docstring tells the model what it does."),
                  ("And the last lines start it, with the streamable HTTP transport, "
                   "so other apps can reach it over a URL."))),
        Scene("", "quiz", "You leave out the transport in mcp.run(). It uses…", items=(
            "Streamable HTTP", "stdio", "Nothing: it fails",
        ), answer=1, caption="No argument means stdio.", said=(
            ("Quick quiz. If you call mcp dot run with no transport at all, what does "
             "it use? Comment your answer."),
            "B, stdio. That's why we ask for streamable HTTP by name.")),
        Scene("Run it and check it", "steps", "Run it and check it", items=(
            "uv run server.py",
            "It listens on 127.0.0.1:8000/mcp",
            "Check it: uv run mcp dev server.py",
        ), said=(
            "Start it with uv run server.py.",
            ("By default it listens on your own machine, port eight thousand, at the "
             "path slash mcp. Printing to the terminal is fine here; only stdio servers "
             "must keep quiet."),
            ("To check it before n8n, run mcp dev. It opens the MCP Inspector, where "
             "you can call opening hours by hand. That part needs Node."))),
        Scene("", "subscribe", "Still building with me?", said=(
            "If you're building along, subscribe, so you don't miss a step.",)),
        Scene("Call it from n8n", "steps", "Call it from n8n", items=(
            "Add the MCP Client node",
            "Server Transport: HTTP Streamable",
            "MCP Endpoint URL: your server's /mcp",
            "Tool: opening_hours · day: Saturday",
        ), said=(
            ("Now n8n. In the project's workflow, add the MCP Client node. It uses an "
             "MCP tool as a normal step."),
            "Set Server Transport to HTTP Streamable.",
            ("Paste your server's address into MCP Endpoint URL. The tool list is "
             "fetched from the server for you."),
            ("Pick opening hours, set the day to Saturday, and run the step. [excited] "
             "There's your answer, from your own code."))),
        Scene("", "pause", "Pause and try this", items=(
            "Run your server",
            "Call it once from the Inspector",
        ), said=(
            "Pause the video, start your server,",
            "and call opening hours once from the Inspector. Resume when it answers.")),
        Scene("Where it runs", "diagram", "Can n8n reach it?", items=(
            "127.0.0.1\nmeans this machine only",
            "n8n elsewhere\nneeds an address it can reach",
            "Agent, not a step?\nuse MCP Client Tool",
        ), said=(
            ("One thing trips people up: can n8n reach your server? By default it "
             "listens on 127.0.0.1, which means this machine only. Host and port are "
             "options you pass to run."),
            ("[sighing] If n8n runs somewhere else, like in the cloud, it can't see "
             "your laptop. Run the server on a machine it can reach."),
            ("And if you'd rather give the tool to an AI agent, use the MCP Client "
             "Tool node instead."))),
        Scene("", "quiz", "An AI Agent should use your tool. Which node?", items=(
            "MCP Client", "MCP Client Tool", "HTTP Request",
        ), answer=1, caption="Agents take MCP tools through MCP Client Tool.", said=(
            ("Last quiz. You want an AI Agent in n8n to use your tool. Which node? "
             "Comment."),
            "B, the MCP Client Tool node. The plain MCP Client is for normal steps.")),
        Scene("Recap", "checklist", "Recap", items=(
            "One tool: opening_hours",
            "MCPServer and @mcp.tool() from the SDK",
            "run(transport=\"streamable-http\")",
            "n8n: the MCP Client node calls it",
        ), said=(
            "Recap. Our server has one tool, with one job: opening hours.",
            "We wrote it with MCPServer and the tool decorator from the official SDK.",
            "We ran it over streamable HTTP, so it has a URL.",
            ("[happy] And n8n calls it with the MCP Client node. Our assistant now asks "
             "our code, instead of guessing."))),
    ),
)
