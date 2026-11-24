"""Local #6: giving Hermes Agent real tools with MCP.

Study note: docs/academy-references/local_06.md

Written 2026-10-18 by the script writer (docs/academy-script-writer.md). No
live demo: no new recording can be made, so the picture is the config on
screen, steps, bullets, a diagram, two quizzes and a pause.

Every product fact was read on the official Hermes Agent docs on
2026-10-18 (New York time):

  hermes-agent.nousresearch.com/docs/user-guide/features/mcp
      ("MCP lets Hermes Agent connect to external tool servers so the agent
      can use tools that live outside Hermes itself"; "MCP support ships
      with the standard install"; "Add an MCP server to
      ~/.hermes/config.yaml" under mcp_servers; the filesystem example,
      command "npx", args ["-y", "@modelcontextprotocol/server-filesystem",
      "/home/user/projects"]; "Hermes will discover the MCP server's tools
      and use them like any other tool"; stdio servers "run as local
      subprocesses", HTTP servers "are remote endpoints Hermes connects to
      directly", url and headers; auth: oauth, tokens "cached at
      ~/.hermes/mcp-tokens/<server>.json"; the catalog of "MCP servers that
      Nous staff has reviewed", "disabled by default", hermes mcp,
      hermes mcp install <name>, "you should still read the manifest before
      installing"; tools: include "Only those MCP server tools are
      registered", exclude "All server tools are registered except the
      excluded ones", "include wins"; registered names
      mcp_<server_name>_<tool_name>; hermes mcp configure <name> "Reopens
      the same checklist"; "Invisible Unicode TAG characters are stripped"
      from tool results; Tools not appearing: "the server failed to
      connect", "discovery failed", "your filter config excluded the
      tools", "the server is disabled with enabled: false"; agent.log names
      every server that did not register; hermes mcp test <name> "reports
      what the server actually answered"; for stdio servers check node
      --version and npx --version)

The planned outline's database server isn't shown on that page, so the
script teaches what it covers (a files server, a remote API server and the
catalog) and overrides the title and summary so nothing promises a
database. The paths and URLs on screen are the page's own examples.
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
  filesystem:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/projects"]
  company_api:
    url: "https://mcp.internal.example.com"
    tools:
      include: [list_orders]"""

PICTURE = (
    "config.yaml\nmcp_servers",
    "Hermes discovers\nthe tools at start",
    "Only the tools\nyou allowed",
)

EPISODE: Episode = scripted(
    next(e for e in LOCAL_PLANNED if e.number == 6),
    title="Give Hermes Agent Tools with MCP: Files, APIs and the Catalog",
    summary=("Hermes Agent has an MCP client built in. Connect a local files server, "
             "a remote API server and a reviewed catalog entry, then limit each one "
             "to the tools it needs."),
    title_hook="Hermes Agent MCP Tools",
    result="Hermes with tools",
    tags=("hermes agent mcp setup", "hermes mcp_servers config",
          "mcp tool filtering", "intermediate", "ATLAS Academy"),
    links=(("Hermes Agent: MCP",
            "https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp"),),
    ask="Which tool would you give your local agent first? Tell me in the comments.",
    topic_said=("Today: Hermes Agent's built-in MCP client, three ways to add tools, "
                "and how to keep each one on a short lead."),
    topic_shown="Tools for Hermes, via MCP",
    payoff=payoff_scene(
        ("By the end, Hermes can read your project folder and call one API, and it "
         "can't touch anything you didn't allow."),
        "mcp_filesystem_read_file · mcp_company_api_list_orders · nothing else",
        items=PICTURE,
        caption="real tools, short lead"),
    hook=Scene("What you'll learn", "hook", "Real tools for Hermes", items=PICTURE,
               code=CONFIG, caption="real tools, short lead", said=(
                   ("Hermes is running locally. [curious] But an agent that can't reach "
                    "your files or apps can only talk."),
                   ("All you need is Hermes Agent from the last episodes, and Node, for "
                    "the files server. MCP support is already built in."))),
    needs=("Hermes Agent, installed", "Node.js with npx, for the files server"),
    scenes=(
        Scene("What MCP adds", "bullets", "What MCP gives Hermes", items=(
            "Tools that live outside Hermes",
            "Local servers and remote ones",
            "Discovered at start, used like any tool",
        ), said=(
            ("MCP lets Hermes use tools that live outside it: your files, a "
             "database, an internal API."),
            ("Servers can be local programs on your machine, or remote ones on the "
             "network. Both go in the same config."),
            ("When Hermes starts, it finds each server's tools, and uses them like "
             "its own. No new code."))),
        Scene("The config", "code", "~/.hermes/config.yaml", code=CONFIG,
              marks=((0, 4), (4, 6), (6, 8)),
              caption="~/.hermes/config.yaml", said=(
                  ("Here's the config, in the dot hermes folder. The first server is "
                   "a local files server. Hermes starts it with npx, and it only sees "
                   "the folder you name."),
                  ("The second is remote: just a web address. If it needs a sign-in, "
                   "auth OAuth makes Hermes handle it, and keep the token on your "
                   "machine."),
                  ("And this include line is the guardrail. Only list orders is "
                   "registered from that server. [emphasis] Everything else stays "
                   "invisible."))),
        Scene("", "quiz", "Include lists one tool. What does Hermes get?", items=(
            "Every tool on the server", "Only that one tool", "No tools at all",
        ), answer=1, caption="include: only those tools are registered.", said=(
            ("Quick quiz. A server has lots of tools, and your include line names "
             "one. What does Hermes get? Comment your answer."),
            "B. Only that one. Include is a whitelist.")),
        Scene("The catalog", "steps", "Or pick from the catalog", items=(
            "hermes mcp: the reviewed catalog",
            "Disabled until you install one",
            "Read its manifest first",
        ), said=(
            ("There's a shortcut too. Run hermes mcp, and you get a catalog of "
             "servers the Nous team has reviewed."),
            ("They're all off until you install one. Pick a row, press enter, and "
             "Hermes walks you through any sign-in."),
            ("But installing runs that server's code. So read its manifest first, "
             "and check where it comes from."))),
        Scene("", "subscribe", "Running AI at home?", said=(
            "If you're building your own local agent, subscribe for the rest.",)),
        Scene("Keep it on a short lead", "diagram", "Limit each tool", items=(
            "include: only these tools",
            "exclude: all but these",
            "hermes mcp configure: change it later",
        ), said=(
            ("Every server can be trimmed. Include keeps only the tools you list. "
             "Use it for anything that can change or delete things."),
            ("Exclude is the opposite: everything except the tools you list. If you "
             "set both, include wins."),
            ("And hermes mcp configure reopens the checklist for a server, so you can "
             "tighten it later."))),
        Scene("Under the hood", "bullets", "What Hermes does for you", items=(
            "Names: mcp_<server>_<tool>",
            "Hidden characters stripped from results",
            "Sign-in tokens kept on your machine",
        ), said=(
            ("A few things happen by themselves. Each tool gets a prefixed name, mcp, "
             "the server, then the tool, so two servers can never clash."),
            ("Hermes also strips invisible characters out of tool results. They're a "
             "known way to sneak hidden instructions past you."),
            ("And sign-in tokens stay in a file in your dot hermes folder, readable "
             "only by you."))),
        Scene("", "pause", "Pause and try this", items=(
            "Add the filesystem server",
            "Ask Hermes to list a folder",
        ), said=(
            ("Pause the video and add the files server to your config, pointing at "
             "one folder you don't mind it reading."),
            ("Start Hermes and ask it to list the files there and sum up what's "
             "inside. Resume when it answers."))),
        Scene("Tools missing?", "steps", "Tools not showing up?", items=(
            "Check agent.log: it names failed servers",
            "hermes mcp test <name>",
            "Filtered out, or enabled: false?",
        ), said=(
            ("[sighing] No tools showing up? First, check the agent log. At startup, "
             "it names every server that didn't connect, and why."),
            ("For a remote server, run hermes mcp test with its name. It shows what "
             "the server really answered. A 401 means the sign-in is wrong."),
            ("Still missing? Your include or exclude list may have filtered it out, "
             "or the server is set to enabled false. For a local one, check that "
             "node and npx work."))),
        Scene("", "quiz", "Exclude lists delete_customer. What's registered?", items=(
            "Only delete_customer", "Everything except delete_customer",
        ), answer=1, caption="exclude: all tools but the listed ones.", said=(
            ("Last quiz. Your exclude line names delete customer. What does Hermes "
             "get? Comment."),
            "B. Every tool except that one. A good habit for anything that deletes.")),
        Scene("Recap", "checklist", "Recap", items=(
            "MCP is built in: config.yaml, mcp_servers",
            "Local servers, remote URLs, or the catalog",
            "include and exclude limit each server",
            "agent.log and hermes mcp test when stuck",
        ), said=(
            "Recap. MCP is built into Hermes: you add servers under mcp servers.",
            "Use a local program, a remote address, or a reviewed catalog entry.",
            "Include and exclude keep each server to the tools you trust.",
            ("[happy] And the agent log and hermes mcp test tell you what went wrong. "
             "Real tools, on a short lead."))),
    ),
)
