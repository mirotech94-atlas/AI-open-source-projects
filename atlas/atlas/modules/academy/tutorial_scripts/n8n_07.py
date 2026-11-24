"""n8n #7: running n8n yourself with Docker, for free.

Study note: docs/academy-references/n8n_07.md

Written 2026-10-18 by the script writer (docs/academy-script-writer.md). No
live demo: no new recording can be made, so the picture is the command on
screen, steps, bullets, a diagram, two quizzes and a lab.

Every product fact was read on the official n8n docs on 2026-10-18:

  docs.n8n.io/choose-how-to-use-n8n.md
      (n8n Cloud "Fully managed, no setup or maintenance required";
      self-hosted "You control the environment and configuration"; "Want to
      run n8n for free: Self-hosted (Community edition)", "Free with almost
      the complete feature set")
  docs.n8n.io/deploy/host-n8n.md
      ("Don't want to read the docs? Run this: curl -fsSL https://get.n8n.io
      | sh", "Requires Docker on Linux or macOS (or WSL on Windows)";
      "Without a license key, n8n runs as the free Community edition")
  docs.n8n.io/deploy/host-n8n/install-options/one-line-setup.md
      ("This method uses Docker"; "Install Docker. This includes Docker
      Compose"; "You don't need to know Docker to use this guide"; it
      "Creates a new folder called n8n", "Writes the configuration files",
      "Downloads n8n and starts it up"; "It's safe to run more than once";
      "n8n is running at: http://localhost:5678", "Data stored in: ./n8n
      (Docker volume: n8n-data)"; "n8n takes a moment to finish starting
      the first time"; everyday commands: stop "docker compose -f
      ./n8n/compose.yml down", start "... up -d", upgrade "curl -fsSL
      https://get.n8n.io | sh -s -- --upgrade", remove "... down -v" then
      rm -rf ./n8n, "-v DELETES all n8n data"; the upgrade "edits two lines
      in .env ... Your data, secrets, and other settings stay as they are";
      "Prefer not to run a script from the internet? You can download and
      read it first"; Windows: run it from WSL; "Only n8n itself (port 5678)
      is ever reachable from outside your machine"; the built-in database
      is SQLite, and for "a team or a production environment, consider ...
      Postgres")
  docs.n8n.io/deploy/host-n8n/install-options/install-with-docker.md
      (marked "This content is outdated", pointing to Docker Compose, which
      the one-line setup writes for you; "n8n recommends self-hosting for
      expert users ... If you aren't experienced at managing servers, n8n
      recommends n8n Cloud")
  docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.scheduletrigger.md
      (the instance time zone "The default is America/New York for
      self-hosted instances", unless the workflow's own time zone is set)
  docs.n8n.io/build/work-with-data/handle-special-data-types/work-with-dates-and-times.md
      ($now, "a Luxon object containing the current timestamp")

The planned outline's "docker run with a volume" is the page n8n now marks
outdated, so the script teaches the one-line setup n8n's docs lead with;
its volume is what keeps the work. The lab uses only the Manual Trigger and
Edit Fields (Set v3.4), no credentials.
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
  "name": "ATLAS Academy lab: my own n8n",
  "nodes": [
    { "name": "Start", "type": "n8n-nodes-base.manualTrigger",
      "typeVersion": 1, "position": [0, 0], "parameters": {} },
    { "name": "Edit Fields", "type": "n8n-nodes-base.set",
      "typeVersion": 3.4, "position": [240, 0],
      "parameters": { "assignments": { "assignments": [
          { "id": "lab7-message", "name": "message",
            "value": "=Hello from my own n8n. It's {{ $now.toFormat('cccc HH:mm') }}.",
            "type": "string" } ] },
        "options": {} } }
  ],
  "connections": {
    "Start": { "main": [[{ "node": "Edit Fields", "type": "main", "index": 0 }]] }
  }
}"""

LAB_FILE = "n8n-my-own-n8n.json"
LAB_FILES: dict[str, str] = {LAB_FILE: LAB_JSON + "\n"}

COMMANDS = """curl -fsSL https://get.n8n.io | sh                      # install, start
docker compose -f ./n8n/compose.yml down                 # stop
docker compose -f ./n8n/compose.yml up -d                # start again
curl -fsSL https://get.n8n.io | sh -s -- --upgrade       # upgrade, data kept"""

PICTURE = (
    "One command\nDocker does the rest",
    "localhost:5678\nyour own editor",
    "A volume\nkeeps your work",
)

EPISODE: Episode = scripted(
    next(e for e in N8N_PLANNED if e.number == 7),
    title_hook="n8n Self-Hosting",
    title_benefit="Run It with Docker",
    result="Your own n8n",
    links=(("One-line setup",
            "https://docs.n8n.io/deploy/host-n8n/install-options/one-line-setup/"),
           ("Choose how to use n8n", "https://docs.n8n.io/choose-how-to-use-n8n/"),
           ("Get Docker", "https://docs.docker.com/get-docker/")),
    ask="Would you run n8n yourself, or stay on Cloud? Tell me why below.",
    topic_said=("Today: n8n on your own computer, with one command, and how to update it "
                "without losing your work."),
    topic_shown="Your own n8n, with Docker",
    payoff=payoff_scene(
        ("By the end, n8n runs on your own machine, it's free, and your workflows "
         "survive every restart and every update."),
        "n8n is running at: http://localhost:5678",
        items=PICTURE,
        caption="your machine, your n8n"),
    hook=Scene("What we'll set up", "hook", "n8n, on your machine", items=PICTURE,
               code=COMMANDS, caption="your machine, your n8n", said=(
                   ("[curious] Ever wondered if you could run n8n yourself, instead of "
                    "in the cloud? You can, for free."),
                   ("All you need is Docker, and a Mac or Linux machine, or Windows with "
                    "WSL. And there's a lab file at the end to prove it works."))),
    needs=("Docker, with Docker Compose", "Linux, macOS, or Windows with WSL",
           "the free lab file"),
    scenes=(
        Scene("Cloud or yourself?", "bullets", "Cloud or self-hosted?", items=(
            "Cloud: n8n runs it for you",
            "Self-hosted: you run it, free Community edition",
            "New to servers? n8n suggests Cloud",
        ), said=(
            ("First, the choice. On n8n Cloud, n8n runs everything for you: hosting, "
             "updates, all of it."),
            ("Self-hosted, you run it yourself. Without a license key, that's the free "
             "Community edition, with almost every feature."),
            ("[emphasis] Be honest with yourself here. n8n says self-hosting suits "
             "people used to managing servers. If that's not you yet, Cloud is "
             "fine."))),
        Scene("Step 1: Docker", "steps", "Step 1: get Docker", items=(
            "Install Docker: it includes Compose",
            "Make sure it's running",
            "Windows? Use WSL",
        ), said=(
            ("Install Docker from Docker's own site. It comes with Docker Compose, "
             "which the setup needs too."),
            ("Start it, and leave it running in the background. You don't need to "
             "know Docker to follow along."),
            ("On Windows, run everything from WSL. The normal command prompt and "
             "PowerShell can't run the setup script."))),
        Scene("Step 2: one command", "code", "Step 2: one command", code=COMMANDS,
              marks=((0, 1), (1, 3), (3, 4)),
              caption="from n8n's one-line setup", said=(
                  ("Open a terminal and run this one line. It checks Docker, makes a "
                   "folder called n8n, writes the settings, and starts n8n."),
                  ("Stopping and starting are these two lines. Same folder, same "
                   "data."),
                  ("And this last one upgrades. It only changes the version numbers. "
                   "[confident] Your workflows and settings stay put."))),
        Scene("", "quiz", "Run the setup a second time. What happens?", items=(
            "It wipes everything", "It says n8n is already there", "It breaks",
        ), answer=1, caption="Running it twice is safe.", said=(
            ("Quick quiz. You run the setup command again by mistake. What happens? "
             "Comment your answer."),
            ("B. It's safe to run twice. It just tells you n8n is already set up, "
             "and changes nothing."))),
        Scene("Step 3: open the editor", "steps", "Step 3: open the editor", items=(
            "Browse to localhost:5678",
            "First start takes a moment",
            "Only port 5678 is reachable",
        ), said=(
            ("When it's ready, it prints the address. Open your browser at localhost, "
             "port five six seven eight."),
            ("The first start takes a moment. If the page doesn't load, wait a bit, "
             "and refresh."),
            ("And only n8n's own port, five six seven eight, can be reached from "
             "outside your machine. Everything else stays private."))),
        Scene("", "subscribe", "Running it yourself?", said=(
            "If you're setting up your own n8n, subscribe for the advanced lessons.",)),
        Scene("", "pause", "Pause and try this", items=(
            "Run the one-line setup",
            "Open localhost:5678 in your browser",
        ), said=(
            ("Pause the video, install Docker if you haven't, and run the one-line "
             "setup."),
            ("Open localhost, port five six seven eight, in your browser. Resume when "
             "you can see the editor."))),
        Scene("Where your work lives", "diagram", "Where your work lives", items=(
            "n8n container\ncan be replaced",
            "n8n-data volume\nyour workflows",
            "down -v\ndeletes it all",
        ), said=(
            ("Here's what matters most. The n8n program runs in a container, and a "
             "container can be removed and replaced, for example when you update."),
            ("Your workflows live in a Docker volume called n8n data. It stays when "
             "the container goes, so your work survives."),
            ("[sighing] But one flag deletes it: down with dash v. Only use that if "
             "you really want everything gone."))),
        Scene("Good habits", "bullets", "Good habits", items=(
            "Read the script first, if you prefer",
            "Upgrade with --upgrade, not by hand",
            "A team? Look at Postgres",
        ), said=(
            ("Not keen on running a script from the internet? Download it, read it, "
             "then run it. n8n's docs show how."),
            "Upgrade with the upgrade flag, so your data and secrets stay as they are.",
            ("And the built-in database is fine to start. For a team, or anything "
             "serious, n8n suggests Postgres."))),
        Scene("", "quiz", "Which command deletes all your n8n data?", items=(
            "up -d", "down", "down -v",
        ), answer=2, caption="The -v removes the volume with your workflows.", said=(
            ("Last quiz. Which of these deletes all your workflows? Comment."),
            ("C. Down with dash v removes the volume too. Plain down just stops "
             "n8n."))),
        lab_scene("Lab: your own n8n", "Lab: your own n8n", LAB_FILE,
                  do=f"Import {LAB_FILE} · Execute workflow",
                  expect="'Hello from my own n8n' + the day and time",
                  stuck="Wrong hour? Set the workflow's time zone",
                  challenge="Stop n8n, start it again: still there?",
                  said=(
                      ("Your lab. Download the lab file from the description, import "
                       "it into your new n8n, and run it."),
                      ("Expect a hello from your own n8n, with today's day and the "
                       "time."),
                      ("Stuck? If the hour looks wrong, a self-hosted n8n starts on New "
                       "York time. Set the workflow's time zone in its settings."),
                      ("Your challenge: stop n8n, start it again, and check that your "
                       "lab workflow is still there."))),
        Scene("Recap", "checklist", "Recap", items=(
            "Self-hosted Community edition: free",
            "One command sets it all up",
            "Open localhost:5678",
            "The volume keeps your work: never down -v",
        ), said=(
            "Recap. Self-hosting gives you the free Community edition.",
            "One command installs and starts it, with Docker doing the heavy lifting.",
            "You open the editor at localhost, port five six seven eight.",
            ("[happy] And the volume keeps your work through restarts and upgrades. "
             "Your own n8n, on your own machine."))),
    ),
)
