"""Approved by Miro 2026-11-24 (he authorised the review); facts re-read that day.

Study note: docs/academy-references/project_09.md

Project #9 (new entry, plan used up): keeping our assistant safe over time,
with backups, workflow exports that carry no secrets, and careful updates.

Written 2026-11-24 as a draft. No live demo: the picture is a diagram, steps,
two commands on screen, bullets, two quizzes and a pause. The episode is for
self-hosted n8n; the Cloud side is not claimed.

Every product fact was read on the official n8n docs on 2026-11-24:

  docs.n8n.io/deploy/host-n8n/keep-n8n-running/backup-and-restore.md
      (back up the .n8n folder, which holds the config file with the
      credential encryption key, and with SQLite the database; an external
      PostgreSQL database with its own tools; stop n8n before copying SQLite
      files; "The encryption key is required to restore credentials"; the
      commands n8n export:workflow --backup --output=... and n8n
      export:credentials --backup --output=...; use a different output
      directory for each; the CLI backup does not include users and roles,
      execution history, variables or instance settings; restore with n8n
      import:workflow --separate and import:credentials --separate;
      credentials need the same key or a --decrypted export, which stores
      secrets in plain text and must be deleted after; take a full backup
      before updating)
  docs.n8n.io/build/manage-workflows/export-and-import.md
      (exported workflow JSON includes credential names and IDs; remove or
      anonymize this information from the JSON file before sharing; HTTP
      Request nodes imported from cURL may contain authentication headers)
  docs.n8n.io/deploy/host-n8n/keep-n8n-running/update-n8n.md
      (update frequently, "at least once a month"; check the release notes
      for breaking changes; test the update on a copy first; follow your
      install method; take a full backup that includes the database and the
      encryption key; pin a specific image tag instead of latest)
  docs.n8n.io/build/manage-workflows/view-change-history.md
      (Workflow history icon; Options > Restore version; the latest version
      is saved before restoring; the last 24 hours for all users, longer
      history on paid plans)

The assistant (form, Claude draft, Sheets log, Slack alert, error workflow)
is the one the series built in project #1 to #8.
"""
from __future__ import annotations

from ..tutorial_series import (
    Episode,
    Scene,
    payoff_scene,
    plan,
    scripted,
)

# Cloud (read 2026-11-24, update-your-version.md): Cloud updates happen in
# Instance settings, and the page says nothing about backups. The script says
# "self-hosted" only and makes no Cloud claim.

# The hashtags are the first three of SERIES_HASHTAGS['project'], written out.
PLANNED = plan(
    9, 3, "project-backup-and-update",
    "Back Up and Update Your Claude + n8n Assistant Without Leaking Secrets",
    "Back up\nsafely",
    ("Back up the n8n instance and its encryption key, export workflows without "
     "secrets, and update on purpose instead of by accident."),
    ("n8n backup", "n8n export workflows", "n8n update", "n8n encryption key"),
    ("#claudeai", "#mcp", "#n8n"),
    ("Three safety nets: back up, export, update",
     "What a backup must hold, and the key",
     "Exporting workflows and credentials with the CLI",
     "Sharing a workflow file without secrets",
     "Updating: notes first, test copy, pinned version",
     "Recap checklist"))

COMMANDS = """n8n export:workflow --backup --output=backups/workflows/
n8n export:credentials --backup --output=backups/credentials/
# two folders, never one"""

PICTURE = (
    "Backup\nfolder, database, key",
    "Export\nfiles without secrets",
    "Update\nnotes, test, pin",
)

EPISODE: Episode = scripted(
    PLANNED,
    title_hook="n8n Backup and Updates",
    title_benefit="No Leaks",
    result="A safe assistant",
    links=(("Back up and restore",
            ("https://docs.n8n.io/deploy/host-n8n/keep-n8n-running/"
             "backup-and-restore/")),
           ("Export and import workflows",
            "https://docs.n8n.io/build/manage-workflows/export-and-import/"),
           ("Update n8n",
            "https://docs.n8n.io/deploy/host-n8n/keep-n8n-running/update-n8n/"),
           ("Workflow history",
            "https://docs.n8n.io/build/manage-workflows/view-change-history/")),
    ask="Have you ever lost work to a bad update? Tell me below.",
    topic_said=("Today: we back up the assistant, share its workflow with no "
                "secrets inside, and update it on purpose."),
    topic_shown="Back up, export, update",
    payoff=payoff_scene(
        ("By the end, you can lose the server and still get the assistant back, "
         "and an update is a plan, not a gamble."),
        "backups/workflows · backups/credentials · the key, stored apart",
        items=PICTURE,
        caption="recoverable, and private"),
    hook=Scene("What we'll do", "hook", "If the server vanished", items=PICTURE,
               code=COMMANDS, caption="recoverable, and private", said=(
                   ("Picture it. [curious] Your server dies tonight. Could you bring "
                    "the assistant back, with every workflow and every credential?"),
                   ("All you need is a self-hosted n8n, with the assistant on it, "
                    "and a terminal on that machine."))),
    needs=("the assistant workflow from the earlier episodes",
           "a self-hosted n8n you control",
           "a terminal on that machine"),
    scenes=(
        Scene("Three safety nets", "diagram", "Back up, export, update", items=(
            "Back up\nthe whole instance",
            "Export\nworkflows as files",
            "Update\non purpose",
        ), said=(
            ("There are three safety nets. First, back up the whole instance, so a "
             "dead server isn't the end."),
            ("Second, export workflows and credentials as files, so you can move "
             "them or restore them one by one."),
            ("Third, update on purpose, with a plan. We'll do all three."))),
        Scene("Back up the instance", "steps", "Step 1: back up the instance", items=(
            "Copy the .n8n folder",
            "PostgreSQL? Back it up with its own tools",
            "SQLite? Stop n8n before copying",
        ), said=(
            ("First, the dot n8n folder. By default it's in your home folder. It "
             "holds a config file, and with SQLite, the database too."),
            ("If you use PostgreSQL, the database lives elsewhere. Back it up with "
             "the database's own tools."),
            ("And with SQLite, stop n8n before you copy the files. Copying while "
             "n8n writes can give you a broken backup."))),
        Scene("The encryption key", "bullets", "The key matters most", items=(
            "It lives in the config file",
            "No key, no credentials",
            "Store a copy somewhere safe",
        ), said=(
            ("Inside that config file is the encryption key. n8n uses it to lock "
             "your credentials."),
            ("[emphasis] The docs are clear. The key is required to restore "
             "credentials. Without it, restored credentials can't be read."),
            ("So keep a copy of the key somewhere safe, and keep it private."))),
        Scene("", "quiz", "You restore credentials but lost the key. What happens?",
              items=("They work", "They can't be decrypted"), answer=1,
              caption="No key, no credentials.", said=(
                  ("Quick quiz. You restored your credentials, but the key is gone. "
                   "What happens? Comment your answer."),
                  "B. They can't be decrypted. The key is the whole point.")),
        Scene("", "subscribe", "Building along?", said=(
            "If this series is helping, subscribe, and the next project is on its way.",)),
        Scene("Export with the command line", "code", "Step 2: export to files",
              code=COMMANDS, marks=((0, 1), (1, 2), (2, 3)),
              caption="one folder for each command", said=(
                  ("Now the files. This command exports every workflow, as separate "
                   "files, with the backup flag."),
                  ("And this one exports every credential. Use a different folder "
                   "for each, or a restore can fail."),
                  ("Good to know: this backup has no users, run history, variables "
                   "or settings. It moves work. It can't rebuild a whole instance."))),
        Scene("", "pause", "Pause and try this", items=(
            "Export your workflows to a folder",
            "Find the encryption key, and store a copy",
        ), said=(
            ("Pause the video and run the workflow export on your own machine."),
            ("Then find your encryption key, and store a copy safely. It takes five "
             "minutes, and it's the best five minutes this month."))),
        Scene("Share a workflow file safely", "bullets", "Before you share the file",
              items=(
                  "The JSON has credential names and IDs",
                  "Remove or anonymize them first",
                  "A pasted cURL can carry auth headers",
              ), said=(
                  ("Sharing a workflow file is different. The exported JSON includes "
                   "your credential names and IDs. The IDs aren't sensitive, but the "
                   "names might be."),
                  ("The docs tell you to remove or anonymize this before sharing. "
                   "Open the file and look."),
                  ("And if you built an HTTP Request from a pasted cURL, it may hold "
                   "an authentication header. Read the file before you send it."))),
        Scene("Update on purpose", "steps", "Step 3: update safely", items=(
            "Read the release notes first",
            "Full backup, then a test copy",
            "Pin a version, not latest",
        ), said=(
            ("Now updates. The docs say update often, at least monthly, so you "
             "never jump many versions. First, read the release notes for breaking "
             "changes."),
            ("Take a full backup, with the database and the key. Then try the "
             "update on a test copy, not on the live one."),
            ("If you use Docker, pin a specific image tag instead of latest. Then "
             "an update only happens when you choose it."))),
        Scene("Undo a bad edit", "bullets", "A bad edit, not a bad update", items=(
            "Workflow history icon",
            "Options, then Restore version",
            "All users get the last 24 hours",
        ), said=(
            ("Not every mistake is an update. Sometimes you just break a workflow. "
             "Open it, and select the Workflow history icon."),
            ("Pick a version, then Options, then Restore version. n8n saves your "
             "current one first, so nothing is lost."),
            ("[confident] Every user gets the last 24 hours. Longer history needs a "
             "paid plan, so don't count on it."))),
        Scene("", "quiz", "Which image tag do you pin?", items=(
            "latest", "A specific version"), answer=1,
              caption="Pin a version, so you choose when it changes.", said=(
                  ("Last quiz. When you pin your n8n image, which tag do you use? "
                   "Comment."),
                  "B, a specific version. Then an update only happens when you decide.")),
        Scene("Recap", "checklist", "Recap", items=(
            "Back up the .n8n folder and database",
            "Keep a safe copy of the encryption key",
            "Share workflow files with no secrets",
            "Update: notes, backup, test copy, pin",
        ), said=(
            "Recap. Back up the dot n8n folder, and the database if you have one.",
            "Keep a safe copy of the encryption key.",
            "Clean a workflow file of names and headers before you share it.",
            ("[happy] And update with a plan: notes, backup, a test copy, and a "
             "pinned version. Now your assistant can survive anything."))),
    ),
)
