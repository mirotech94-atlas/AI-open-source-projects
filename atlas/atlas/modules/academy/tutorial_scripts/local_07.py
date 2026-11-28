"""Local #7: Hermes Agent as an MCP server for Claude Desktop.

Study note: docs/academy-references/local_07.md

Approved by Miro 2026-11-24 (he authorised the review); facts re-read that day.

Written 2026-11-24. No live demo: the picture is the config on screen, steps,
bullets, a diagram, two quizzes and a pause.

Read on 2026-11-24 (New York time):

  hermes-agent.nousresearch.com/docs/user-guide/features/mcp
      (Hermes can act as an MCP server through the official mcp-serve
      plugin; "hermes plugins install mcp-serve"; Claude Desktop config in
      the standard mcpServers format with command "hermes" and args
      ["mcp-serve"]; the older built-in spelling "hermes mcp serve" and its
      args ["mcp", "serve"] should change to ["mcp-serve"]; tool names and
      parameters unchanged; the server exposes messaging conversations:
      list, read, wait for new messages, send across connected platforms;
      optionally Hermes' web, browser, vision, image, skills and TTS tools;
      the --tools mode and its safety limits live in the plugin README)
  Sibling module local_06 (Hermes is an MCP client: mcp_servers in
      ~/.hermes/config.yaml).

  modelcontextprotocol.io/docs/develop/connect-local-servers
      (Claude Desktop: Settings, Developer tab, Edit Config; file at
      ~/Library/Application Support/Claude/claude_desktop_config.json on macOS;
      fully quit and restart Claude Desktop after saving; troubleshooting: restart,
      check the JSON syntax, check paths, read the logs, run the server command by
      hand)

Not claimed: what the --tools mode allows (the Hermes page only points to the
plugin README), and where Hermes keeps its keys (the page does not say).
"""
from __future__ import annotations

from ..tutorial_series import (
    LOCAL_PLANNED,
    Episode,
    Scene,
    payoff_scene,
    scripted,
)

CONFIG = """{
  "mcpServers": {
    "hermes": {
      "command": "hermes",
      "args": ["mcp-serve"]
    }
  }
}"""

PICTURE = (
    "hermes plugins install\nmcp-serve",
    "mcpServers entry\nin Claude Desktop",
    "Claude reaches\nyour conversations",
)

EPISODE: Episode = scripted(
    next(e for e in LOCAL_PLANNED if e.number == 7),
    title="Use Hermes Agent from Claude Desktop with MCP Server Mode",
    title_hook="Hermes MCP Server",
    title_benefit="Use it in Claude",
    result="Claude reads Hermes",
    tags=("hermes agent mcp server", "claude desktop mcp", "hermes mcp-serve",
          "advanced", "ATLAS Academy"),
    links=(("Hermes Agent: MCP",
            "https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp"),),
    ask="What would you ask Claude to look up in your Hermes chats? Tell me below.",
    topic_said=("Today: the other side of MCP. Hermes as a server, and Claude Desktop "
                "as the client that connects to it."),
    topic_shown="Hermes as an MCP server",
    payoff=payoff_scene(
        ("By the end, Claude Desktop will list your Hermes conversations, and you'll "
         "know exactly what it can reach."),
        "hermes plugins install mcp-serve · one entry in Claude Desktop's config",
        items=PICTURE,
        caption="Hermes as a server"),
    hook=Scene("What you'll learn", "hook", "Hermes inside Claude", items=PICTURE,
               code=CONFIG, caption="Hermes as a server", said=(
                   ("Last time, Hermes used other people's tools. [curious] What if "
                    "Claude Desktop could use Hermes?"),
                   ("You'll need Hermes Agent installed, and Claude Desktop. That's it."))),
    needs=("Hermes Agent, installed", "Claude Desktop"),
    scenes=(
        Scene("Client or server", "bullets", "Two sides of MCP", items=(
            "Client mode: Hermes uses outside tools",
            "Server mode: outside apps use Hermes",
            "Same protocol, opposite direction",
        ), said=(
            ("In the last episode, Hermes was the client. It reached out to tool "
             "servers listed in its config."),
            ("Server mode flips it. Now Hermes waits, and an app like Claude Desktop "
             "connects in."),
            ("It's the same protocol both ways. Only the direction changes."))),
        Scene("Why bother", "bullets", "Why connect them", items=(
            "Ask Claude about your Hermes chats",
            "One window for both assistants",
            "Your agent keeps running as before",
        ), said=(
            ("Why do this? You can ask Claude to find a conversation, read it, and "
             "pull out what you need. No copying and pasting between apps."),
            ("It also means you can work in one window, while Hermes carries on "
             "with its own chats in the background."),
            ("Nothing about your agent changes. Claude just gets a door into it, and "
             "you decide how wide that door is."))),
        Scene("Install the plugin", "steps", "Start the server", items=(
            "hermes plugins install mcp-serve",
            "Add one entry to Claude Desktop",
            "Restart Claude Desktop",
        ), said=(
            ("Server mode comes as an official plugin. Install it with hermes plugins "
             "install mcp dash serve."),
            ("Then tell Claude Desktop how to start it. That's one entry in its "
             "config file."),
            ("Restart the app so it picks the entry up. [relaxed] You never run the "
             "server by hand."))),
        Scene("The config", "code", "Claude Desktop config", code=CONFIG,
              marks=((0, 2), (2, 6)),
              caption="mcpServers entry", said=(
                  ("This is the standard mcpServers format. You add it to Claude "
                   "Desktop's config file."),
                  ("The command is hermes, and the argument is mcp dash serve. "
                   "Claude starts it, and talks to it directly."))),
        Scene("", "quiz", "Who starts the Hermes server?", items=(
            "You, in a terminal", "Claude Desktop, from the config",
        ), answer=1, caption="Claude runs the command in the entry.", said=(
            ("Quick quiz. In this setup, who starts the Hermes server? Comment your "
             "answer."),
            "B. Claude Desktop runs the command from its config.")),
        Scene("What Claude can reach", "bullets", "What it exposes", items=(
            "Your messaging conversations: list and read",
            "Wait for new messages, and send them",
            "Optionally web, browser, vision and more",
        ), said=(
            ("The docs say the server exposes your messaging conversations. Claude "
             "can list them and read them."),
            ("It can also wait for new messages, and send messages across the "
             "platforms Hermes is connected to."),
            ("And you can choose to expose Hermes' own tools, like web, browser "
             "and vision."))),
        Scene("The edges", "bullets", "What Claude can and can't see", items=(
            "Only what the server lists",
            "Extra tools are opt-in",
            "You choose what you expose",
        ), said=(
            ("Claude only sees what the server offers. If a thing isn't on the "
             "list, Claude can't ask for it. That's the whole point of the protocol."),
            ("The extra tools, like web and vision, are optional. Leave them off "
             "until you have a reason, and you've shrunk what Claude can touch."),
            ("You decide what to switch on, and you can look at the list before you "
             "restart. Treat each tool as something Claude may use."))),
        Scene("", "subscribe", "Running AI at home?", said=(
            "If you're building your own local agent, subscribe for the rest.",)),
        Scene("Keep it safe", "steps", "Know what you hand over", items=(
            "Sending is real: messages go out",
            "Read the plugin README for --tools",
            "Check your config before you restart",
        ), said=(
            ("Sending is real. If Claude sends a message, it lands in a real chat, "
             "so treat this link like a key."),
            ("The docs point to the plugin's README for the tools mode and its "
             "limits. Read it first."),
            ("And if you used the older hermes mcp serve spelling, switch the "
             "argument to mcp dash serve."))),
        Scene("", "pause", "Pause and try this", items=(
            "Install the plugin",
            "Add the entry and restart",
        ), said=(
            ("Pause the video and install the plugin. Then add the entry from the "
             "screen to Claude Desktop's config."),
            ("Restart Claude, and ask it to list your Hermes conversations. Resume "
             "when it answers."))),
        Scene("If it fails", "diagram", "Nothing shows up?", items=(
            "Did you fully restart Claude?",
            "Does the command run by hand?",
            "Is the JSON valid?",
        ), said=(
            ("[sighing] No tools showing in Claude? First, quit Claude Desktop "
             "fully, and start it again."),
            ("Then run the same command in a plain terminal. If that fails, Claude "
             "can't start it either."),
            ("Last, look at the JSON. One missing comma breaks the whole file."))),
        Scene("", "quiz", "Which args start server mode now?", items=(
            "mcp, serve", "mcp-serve",
        ), answer=1, caption="The plugin spelling is mcp-serve.", said=(
            ("Last quiz. Which argument starts server mode with the plugin? Comment."),
            "B. mcp dash serve, one word. The old two-word form is legacy.")),
        Scene("Recap", "checklist", "Recap", items=(
            "Server mode: apps connect in to Hermes",
            "Install the mcp-serve plugin",
            "One mcpServers entry in Claude Desktop",
            "It exposes your conversations, so guard it",
        ), said=(
            "Recap. In server mode, outside apps connect in to Hermes.",
            "Install the mcp dash serve plugin first.",
            "Then add one mcpServers entry to Claude Desktop.",
            ("[happy] It can read and send your messages, so guard it like a key."))),
    ),
)
