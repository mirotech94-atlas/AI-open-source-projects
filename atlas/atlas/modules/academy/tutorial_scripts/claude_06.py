"""Claude #6: a custom Claude connector, a remote MCP server with OAuth.

Study note: docs/academy-references/claude_06.md

Written 2026-10-18 by the script writer (docs/academy-script-writer.md). No
live demo: no new recording can be made, so the picture is a diagram,
bullets, steps, the sign-in handshake and the commands on screen, two
quizzes and a pause. It explains how the pieces fit; it builds no server.

Every product fact was read on Anthropic's official docs on 2026-10-18:

  claude.com/docs/connectors/building/mcp.md
      (Local MCP "Runs on the user's device", Remote MCP "Hosted on the
      internet"; a server exposes tools, resources and prompts)
  claude.com/docs/connectors/building/index.md
      ("A remote server runs on infrastructure you host and is reachable
      over the internet. Claude connects to it from every Claude app"; "Use
      Streamable HTTP"; "Decide on authentication before you write tool
      code"; OAuth 2.0 "each user signs in with their own account", "a
      static credential", or "no authentication at all"; DCR; redirect
      https://claude.ai/api/mcp/auth_callback and a loopback redirect for
      Claude Code; "Claude refreshes access tokens when they expire"; "As a
      custom connector: a user or an organization Owner adds it by
      entering its URL, with no review by Anthropic")
  claude.com/docs/connectors/building/authentication.md
      (the three ways in; "A 401 is required to start sign-in, and Claude
      ignores a WWW-Authenticate header on a 200 response"; the 401 with
      WWW-Authenticate: Bearer resource_metadata="https://mcp.example.com/
      .well-known/oauth-protected-resource"; "the document's resource
      field must equal the URL as the user enters it"; Claude Code's
      loopback redirect "on an ephemeral port", such as
      http://localhost:3118/callback; a discovery failure: "your MCP server
      receives the initial request but your authorization server sees no
      traffic at all")
  claude.com/docs/connectors/custom/add-unlisted.md
      (Free, Pro or Max: Customize > Connectors, "Add custom connector",
      the server URL, Add; "On the Free plan, you can add one custom
      connector"; Team and Enterprise: an Owner adds it, members connect
      with their own account; MCP server URL, "for example
      https://mcp.example.com/mcp"; "Custom connectors allow connections to
      unverified services"; "Only connect to servers from trusted
      organizations", "Carefully review requested permission scopes")
  code.claude.com/docs/en/mcp
      ("HTTP servers are the recommended option for connecting to remote
      MCP servers"; claude mcp add --transport http <name> <url>; stdio
      servers "run as local processes on your machine"; "Claude Code
      supports OAuth 2.0"; run /mcp, "Then follow the steps in your browser
      to log in"; "Authentication tokens are stored securely and refreshed
      automatically"; "Use Clear authentication in the /mcp menu to revoke
      access"; ${VAR} expansion in .mcp.json, in url and headers, "while
      maintaining flexibility for ... sensitive values like API keys")

The mcp.example.com addresses are the docs' own placeholders.
"""
from __future__ import annotations

from ..tutorial_series import (
    CLAUDE_PLANNED,
    Episode,
    Scene,
    payoff_scene,
    scripted,
)

HANDSHAKE = """HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer resource_metadata=
  "https://mcp.example.com/.well-known/oauth-protected-resource\""""

ADD = """# claude.ai: Customize > Connectors > Add custom connector
#   MCP server URL: https://mcp.example.com/mcp
# Claude Code:
claude mcp add --transport http shop https://mcp.example.com/mcp
/mcp        # then sign in, in your browser"""

PICTURE = (
    "Your MCP server\non the internet",
    "OAuth sign-in\neach person, own account",
    "Claude\nin every app",
)

EPISODE: Episode = scripted(
    next(e for e in CLAUDE_PLANNED if e.number == 6),
    title_hook="Custom Claude Connectors",
    title_benefit="Remote MCP",
    links=(("Build an MCP server for Claude",
            "https://claude.com/docs/connectors/building/index"),
           ("Authentication for connectors",
            "https://claude.com/docs/connectors/building/authentication"),
           ("Add a connector that isn't in the directory",
            "https://claude.com/docs/connectors/custom/add-unlisted"),
           ("Claude Code and MCP", "https://code.claude.com/docs/en/mcp")),
    ask="Which of your own tools would you turn into a connector? Tell me below.",
    topic_said=("Today: how a custom connector reaches Claude, where OAuth sign-in fits, "
                "and how to add one."),
    topic_shown="Remote MCP, with sign-in",
    payoff=payoff_scene(
        ("By the end, you'll know how your own system can show up in Claude as a "
         "connector, with every person signing in as themselves."),
        "Customize > Connectors > shop · Connected",
        items=PICTURE,
        caption="your tools, signed in safely"),
    hook=Scene("What you'll learn", "hook", "Your system, in Claude", items=PICTURE,
               code=ADD, caption="your tools, signed in safely", said=(
                   ("Back in episode one, our MCP server ran on your own computer. "
                    "[curious] What if your whole team, on any device, could use it?"),
                   ("That's a custom connector. All you need today is Claude, and the "
                    "address of a remote MCP server, yours or one you trust."))),
    needs=("a Claude account", "Claude Code, for the command-line part",
           "the URL of a remote MCP server you trust"),
    scenes=(
        Scene("Local or remote", "diagram", "Local or remote?", items=(
            "Local server\nruns on your device",
            "Remote server\nhosted on the internet",
            "Claude\nreaches it from every app",
        ), said=(
            ("Two kinds of MCP server. A local one runs on your own device, like the "
             "ones we've built so far."),
            ("A remote one is hosted on the internet, at a web address. It speaks a "
             "transport called Streamable HTTP."),
            ("And because it's online, Claude can reach it from every app: the web, "
             "the desktop, your phone, and Claude Code."))),
        Scene("Three ways in", "bullets", "How Claude gets in", items=(
            "OAuth: each person signs in",
            "A fixed key in a header",
            "No sign-in at all",
        ), said=(
            ("Before you write any tools, decide how people get in. The first way is "
             "OAuth: each person signs in with their own account."),
            ("The second is one fixed key, sent in a header. Handy for one shared "
             "internal tool."),
            ("The third is no sign-in. Anyone with the address gets in. [emphasis] "
             "Fine for public data, never for customer data."))),
        Scene("", "quiz", "Each person should see only their own data. Pick…", items=(
            "No sign-in", "One shared key", "OAuth",
        ), answer=2, caption="OAuth: everyone signs in as themselves.", said=(
            ("Quick quiz. Each person must see only their own orders. Which way in? "
             "Comment your answer."),
            "C, OAuth. Everyone signs in as themselves, so the server knows who's asking.")),
        Scene("How sign-in starts", "code", "How sign-in starts", code=HANDSHAKE,
              marks=((0, 1), (1, 3)),
              caption="the server's first answer", said=(
                  ("So how does Claude know to sign in? The server answers its first "
                   "request with a 401, unauthorized. That status is required."),
                  ("And with it, a header pointing to a small document that names your "
                   "sign-in server. Claude reads it, and starts the OAuth flow."))),
        Scene("", "subscribe", "Still with me?", said=(
            "If you'd like the rest of these Claude lessons, subscribe.",)),
        Scene("What the sign-in side allows", "bullets", "What sign-in must allow",
              items=(
                  "claude.ai's callback address",
                  "Claude Code: localhost, any port",
                  "Client registration, done for you",
              ), said=(
                  ("Your sign-in server has to send people back to Claude. For the "
                   "Claude apps, that's one fixed callback address on claude dot ai."),
                  ("Claude Code comes back to localhost instead, on a port that changes "
                   "each session. So allow any port."),
                  ("And Claude can register itself with your sign-in server, using "
                   "Dynamic Client Registration. One less thing for you to set up."))),
        Scene("Add it to Claude", "code", "Add it to Claude", code=ADD,
              marks=((0, 2), (2, 5)),
              caption="the docs' example address", said=(
                  ("In Claude, open Customize, then Connectors, and choose Add custom "
                   "connector. Paste the server's address, and click Add. On the free "
                   "plan, you get one."),
                  ("In Claude Code, it's claude mcp add, with transport http, a name, "
                   "and the address. Then type slash mcp, and sign in in your "
                   "browser."))),
        Scene("", "pause", "Pause and try this", items=(
            "Pick one remote server you trust",
            "Add it, and sign in",
        ), said=(
            "Pause the video and pick one remote MCP server from a company you trust.",
            "Add it as a custom connector, sign in, and resume.")),
        Scene("Keep secrets out", "bullets", "Secrets stay out", items=(
            "Never a key in your code",
            "${API_KEY} in .mcp.json, not the key",
            "/mcp → Clear authentication to revoke",
        ), said=(
            ("Now, keeping it safe. A key never goes in your code, or in a file you "
             "commit."),
            ("In a shared dot mcp dot json, write dollar, brace, API key, brace. "
             "Claude Code fills in the real value from your machine."),
            ("Sign-in tokens are stored securely and refreshed for you. To cut access "
             "off, open slash mcp and choose Clear authentication."))),
        Scene("When sign-in fails", "steps", "Sign-in never starts?", items=(
            "Server gets the request, sign-in server nothing",
            "Check: a 401, with the resource_metadata pointer",
            "Check: resource matches the URL exactly",
        ), said=(
            ("[sighing] A classic failure. Your MCP server sees Claude's request, but "
             "your sign-in server sees nothing at all."),
            ("That means Claude never found the pointer. Check the server answers 401, "
             "with that header."),
            ("And check the document's resource matches the address people type, "
             "path and all."))),
        Scene("", "quiz", "A 200 reply with the sign-in header. Does sign-in start?",
              items=("Yes", "No, a 401 is needed"), answer=1,
              caption="Claude ignores the header on a 200.", said=(
                  ("Last quiz. Your server replies 200, okay, but includes the sign-in "
                   "header. Does sign-in start? Comment."),
                  "B, no. Claude needs the 401. On a 200, it ignores the header.")),
        Scene("Recap", "checklist", "Recap", items=(
            "Remote server: online, Streamable HTTP",
            "OAuth: everyone signs in as themselves",
            "Sign-in starts with a 401",
            "Add by URL · keys never in code",
        ), said=(
            "Recap. A custom connector is a remote MCP server, online, over Streamable HTTP.",
            "With OAuth, everyone signs in as themselves.",
            "Sign-in starts when the server answers 401, pointing to its sign-in server.",
            ("[happy] And you add it by its address, with keys kept out of your code. "
             "Only connect servers you trust."))),
    ),
)
