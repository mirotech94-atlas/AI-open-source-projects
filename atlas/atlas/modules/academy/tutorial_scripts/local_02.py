"""Local #2: installing Hermes Agent and giving it its first tool.

Written 2026-10-17 by the script writer (docs/academy-script-writer.md). No
live demo: no new recording can be made, so the picture is the commands on
screen, steps, bullets, two quizzes and a pause.

Every product fact was read on the official Hermes Agent pages on
2026-10-17:

  raw.githubusercontent.com/NousResearch/hermes-agent/main/README.md
      (Linux, macOS, WSL2: curl -fsSL
      https://hermes-agent.nousresearch.com/install.sh | bash; native
      Windows and Android/Termux have their own installers; after install
      "source ~/.bashrc" (or ~/.zshrc), then "hermes" to start chatting;
      hermes setup "Run the full setup wizard"; hermes model "Choose your
      LLM provider and model"; hermes tools "Configure which tools are
      enabled"; hermes doctor "Diagnose any issues"; hermes update; Nous
      Portal, OpenRouter, OpenAI, your own endpoint)
  hermes-agent.nousresearch.com/docs/user-guide/features/tools
      (tools "organized into logical toolsets that can be enabled or
      disabled per platform"; toolsets include web, terminal, file;
      hermes tools to configure them; hermes chat --toolsets "web,terminal")
  hermes-agent.nousresearch.com/docs/user-guide/security
      ("Before executing any command, Hermes checks it against a curated
      list of dangerous patterns"; once / session / always / deny
      (default); "If no response is given within the timeout, the command
      is denied by default (fail-closed)")
  hermes-agent.nousresearch.com/docs/reference/faq
      ("hermes: command not found": the shell "hasn't reloaded the updated
      PATH"; source ~/.bashrc or ~/.zshrc, or a new terminal; API key not
      working: run hermes model to reconfigure)

A hosted provider first, as the plan says: a local model comes later in the
series. No price, model size or speed is said.
"""
from __future__ import annotations

from ..tutorial_series import (
    LOCAL_PLANNED,
    Episode,
    Scene,
    payoff_scene,
    scripted,
)

INSTALL = """curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
source ~/.bashrc        # or ~/.zshrc
hermes setup            # the setup wizard
hermes model            # pick a provider and a model
hermes tools            # turn on one toolset: file
hermes                  # start chatting"""

PICTURE = (
    "Install\none line in the terminal",
    "Pick a model\nand one tool",
    "First task\nit reads your files",
)

EPISODE: Episode = scripted(
    next(e for e in LOCAL_PLANNED if e.number == 2),
    title_hook="Hermes Agent",
    links=(("Hermes Agent on GitHub", "https://github.com/NousResearch/hermes-agent"),
           ("Tools and toolsets",
            "https://hermes-agent.nousresearch.com/docs/user-guide/features/tools"),
           ("Security and command approval",
            "https://hermes-agent.nousresearch.com/docs/user-guide/security"),
           ("FAQ and troubleshooting",
            "https://hermes-agent.nousresearch.com/docs/reference/faq")),
    ask="Which tool would you give Hermes first? Tell me in the comments.",
    topic_said=("Today: installing Hermes Agent, choosing a model, turning on one "
                "tool, and running a first task."),
    topic_shown="Install Hermes Agent",
    payoff=payoff_scene(
        ("By the end, Hermes Agent is installed, talking to a model you chose, and "
         "reading files in a folder you point it at."),
        "hermes → 'summarise the notes in this folder' → done",
        items=PICTURE,
        caption="installed, with one tool"),
    hook=Scene("What we'll do", "hook", "From zero to a first task", items=PICTURE,
               code=INSTALL, caption="installed, with one tool", said=(
                   ("[happy] Last time we met Hermes Agent. Today, it goes on your "
                    "computer."),
                   ("All you need is a Mac, Linux or WSL2 terminal, and an account with "
                    "a model provider."
             " Hosted for now; running the model yourself comes later in this series."))),
    needs=("a Mac, Linux or WSL2 terminal", "an account with a model provider",
           "a test folder with a few text files"),
    scenes=(
        Scene("Step 1: install", "steps", "Step 1: install", items=(
            "Run the one-line installer",
            "Reload your shell: source ~/.bashrc",
            "Windows and Android have their own",
        ), said=(
            ("Open a terminal and paste the official one-line installer from the "
             "Hermes README. It's on screen, and in the description."
             " It works on Linux, macOS and WSL2."),
            ("When it finishes, reload your shell, with source and your bash or zsh "
             "file, or simply open a new terminal."
             " Skip this, and the hermes command won't be found yet."),
            ("On native Windows or Android, use their own installers from the same "
             "page instead."))),
        Scene("Step 2: setup and model", "code", "Step 2: setup and a model",
              code=INSTALL, marks=((2, 3), (3, 4)),
              caption="the official commands, from the README", said=(
                  ("Now type hermes setup. It's a wizard that walks you through "
                   "everything at once."
             " Take your time here; you can change any of it later."),
                  ("To change the model later, run hermes model. You can use Nous "
                   "Portal, OpenRouter, OpenAI, or your own endpoint. [emphasis] Hermes "
                   "is the agent; the model is your choice."))),
        Scene("", "quiz", "You just installed it. 'hermes: command not found'. Why?",
              items=(
                  "It didn't install", "Your shell hasn't reloaded its PATH",
                  "You need a new computer",
              ), answer=1, caption="Reload the shell, or open a new terminal.", said=(
                  ("Quick quiz. The install finished, but the terminal says hermes, "
                   "command not found. Most likely? Comment your answer."),
                  ("B. The shell hasn't reloaded its PATH yet. Source your shell file, "
                   "or open a new terminal."))),
        Scene("Step 3: one tool", "steps", "Step 3: one tool, only one", items=(
            "hermes tools",
            "Turn on the file toolset",
            "Leave the rest off for now",
        ), said=(
            ("Tools come in groups called toolsets, like web, terminal and file. Run "
             "hermes tools to choose them."),
            ("Turn on the file toolset, so Hermes can read files."
             " That's all our first task needs."),
            ("And leave the others off for now. [confident] One tool you understand "
             "beats twenty you don't."))),
        Scene("", "subscribe", "Still with me?", said=(
            "If you're setting up your own agent with me, subscribe.",)),
        Scene("Step 4: a first task", "steps", "Step 4: a first task", items=(
            "cd into a test folder",
            "Type hermes",
            "Ask: 'summarise the notes in this folder'",
        ), said=(
            ("Make a test folder with a few text notes in it, and move into it in the "
             "terminal."
             " Use copies, never your only version of anything."),
            ("Type hermes to start chatting."
             " You're now talking to your agent, right in the terminal."),
            ("Then ask it to summarise the notes in this folder. It reads them with "
             "its file tool, and answers. [excited] Your agent's first job, done."))),
        Scene("", "pause", "Pause and try this", items=(
            "Install, setup, one toolset",
            "Ask it about your test folder",
        ), said=(
            "Pause the video and install Hermes, run setup, and turn on one toolset.",
            "Then ask it about your test folder. Resume when it answers.")),
        Scene("When it asks first", "bullets", "When Hermes asks first", items=(
            "Risky commands are checked first",
            "You choose: once, session, always, deny",
            "No answer in time? It's denied",
        ), said=(
            ("Before Hermes runs a command, it checks it against a list of dangerous "
             "patterns, like deleting folders."),
            ("If one matches, it asks you: once, for this session, always, or deny. "
             "When in doubt, deny."
             " Always means it's saved for good, so keep that one for commands you know well."),
            ("And if you don't answer in time, the command is denied. Silence never "
             "counts as a yes."
             " That's exactly how an agent should behave."))),
        Scene("Not working?", "bullets", "Not working?", items=(
            "Command not found: reload the shell",
            "Key or model wrong: hermes model",
            "Anything else: hermes doctor",
        ), said=(
            ("[sighing] Something off? Command not found means the shell needs "
             "reloading."),
            ("A key that doesn't work, or a model that isn't found? Run hermes model "
             "again and pick it fresh."
             " Most of the time, that's the whole fix."),
            "And for anything else, hermes doctor diagnoses the problem for you.")),
        Scene("", "quiz", "Hermes asks to run a command you don't recognise. You…",
              items=("Pick always", "Pick deny", "Walk away"),
              answer=1, caption="Deny is the safe default; walking away denies too.",
              said=(
                  ("Last quiz. Hermes asks to run a command you don't recognise. What "
                   "do you pick? Comment."),
                  ("B, deny. And walking away would deny it too, after the timeout."
             " Ask it what the command does first, if you're curious."))),
        Scene("Recap", "checklist", "Recap", items=(
            "One-line install, then reload the shell",
            "hermes setup · hermes model",
            "hermes tools: one toolset to start",
            "Approvals and hermes doctor keep you safe",
        ), said=(
            "Recap. Install with the one-line script, then reload your shell.",
            "Hermes setup walks you through it, and hermes model picks the model.",
            ("Hermes tools turns on one toolset, the file tools, to start."
             " Add more only when you need them."),
            ("[happy] And approvals and the doctor command keep things safe. Your own "
             "agent, installed and working."))),
    ),
)
