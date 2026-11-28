"""Approved by Miro 2026-11-24 (he authorised the review); facts re-read that day.

Study note: docs/academy-references/project_07.md

Project #7: sharing our assistant with the team, safely.

Written 2026-11-24 as a draft. No live demo: the picture is a diagram, steps,
a code file on screen, bullets, two quizzes and a pause.

Every product fact was read on the official docs on 2026-11-24:

  docs.n8n.io/build/manage-workflows/share-with-others.md
      (workflow sharing: n8n Cloud all plans, self-hosted Business and
      Enterprise; Share > Add users > Save; only for workflows in a Personal
      workspace, for a project workflow "add them to the project instead";
      editors "can't share or delete the workflow" and can't edit nodes that
      use credentials the owner hasn't shared; with unshared credentials you
      can view, run and edit the other nodes)
  docs.n8n.io/administer/manage-credentials/share-credentials-securely.md
      (recipients get "Can use": they run and publish workflows with the
      credential but "can't see or edit its details"; Credentials > pick one
      > Sharing > "Share with projects or users" > Save)
  docs.n8n.io/administer/manage-users-and-access/set-permissions-and-roles-rbac/
  understand-instance-roles.md
      (Owner, Admin, Member; Admin needs Cloud Pro or Enterprise, or
      self-hosted Business or Enterprise)
  docs.n8n.io/administer/manage-users-and-access/set-permissions-and-roles-rbac/
  understand-project-roles.md
      (project Admin manages members and settings, Editor changes workflows
      and credentials, Viewer is read-only; no project roles on the Community
      editions)
  code.claude.com/docs/en/mcp.md
      (scopes local, project, user; project scope is stored in .mcp.json at
      the project root and shared "via version control"; Claude Code asks you
      to approve project servers; ${VAR} expands in url and headers)
  code.claude.com/docs/en/skills.md
      (project skills live in .claude/skills/<name>/SKILL.md and can be
      committed so the whole team gets them)

The assistant (form, Claude draft, Sheets log, Slack alert) is the one the
series built in project #1 to #6.
"""
from __future__ import annotations

from ..tutorial_series import (
    PROJECT_PLANNED,
    Episode,
    Scene,
    payoff_scene,
    scripted,
)

MCP_FILE = """{
  "mcpServers": {
    "assistant": {
      "type": "http",
      "url": "${ASSISTANT_MCP_URL}"
    }
  }
}"""

PICTURE = (
    "Teammates\nuse and edit it",
    "Credentials\nshared, never shown",
    "Settings in the repo\none setup for all",
)

EPISODE: Episode = scripted(
    next(e for e in PROJECT_PLANNED if e.number == 7),
    title_hook="n8n Team Sharing",
    title_benefit="Safely",
    result="A shared assistant",
    tags=("n8n share workflow", "n8n share credentials", "n8n project roles",
          "claude code mcp.json", "advanced", "ATLAS Academy"),
    links=(("Share workflows",
            "https://docs.n8n.io/build/manage-workflows/share-with-others/"),
           ("Share credentials",
            ("https://docs.n8n.io/administer/manage-credentials/"
             "share-credentials-securely/")),
           ("Claude Code MCP scopes", "https://code.claude.com/docs/en/mcp"),
           ("Claude Code skills", "https://code.claude.com/docs/en/skills")),
    ask="Who would you share your assistant with first? Tell me below.",
    topic_said=("Today: we hand the assistant to the team, and nobody ever sees "
                "a secret."),
    topic_shown="Share it, without sharing secrets",
    payoff=payoff_scene(
        ("By the end, your teammates can open and run the assistant, and your "
         "keys stay yours."),
        "Teammate opens the workflow · runs it · can't see the credential",
        items=PICTURE,
        caption="shared, and still safe"),
    hook=Scene("What we'll build", "hook", "Hand it to the team", items=PICTURE,
               code=MCP_FILE, caption="shared, and still safe", said=(
                   ("The assistant works. [curious] Now someone else wants to use "
                    "it. Do you hand over your password?"),
                   ("You won't. All you need is the assistant from the earlier "
                    "parts, a teammate, and a plan that has sharing."))),
    needs=("the assistant workflow from the earlier episodes",
           "a teammate with an account on the same n8n",
           "a plan that includes sharing"),
    scenes=(
        Scene("Who needs what", "diagram", "Look, change, manage", items=(
            "Look and run\nViewer",
            "Change it\nEditor",
            "Manage people\nAdmin",
        ), said=(
            ("First, decide who needs what. Some people only need to look at the "
             "assistant. That's a project Viewer, and it's read-only."),
            ("Some need to change it. That's an Editor. They can build, but they "
             "can't manage the people."),
            ("And one or two manage the project: the Admins. Give people the least "
             "they need. It's much easier to add access later than to take it back."))),
        Scene("Share the workflow", "steps", "Step 1: share the workflow", items=(
            "Open the workflow, select Share",
            "Add users, then Save",
            "In a project? Add them to the project",
        ), said=(
            ("Open the assistant's workflow, the one we built across the earlier "
             "parts, and select Share."),
            ("Under Add users, pick your teammate, and Save. They get editor "
             "access, so they can open it, change it and run it. They can't share it or delete it. Only you can."),
            ("That button only works for workflows in your personal space. For a "
             "workflow inside a project, add the person to the project instead."))),
        Scene("", "quiz", "A teammate is an editor on your workflow. Can they delete it?",
              items=("Yes", "No"), answer=1,
              caption="Editors can't share or delete the workflow.", said=(
                  ("Quick quiz. Your teammate is an editor on the workflow. Can "
                   "they delete it? Comment your answer."),
                  "B, no. Editors can't share it or delete it.")),
        Scene("", "subscribe", "Building along?", said=(
            "If you're building this assistant with me, subscribe for the next part.",)),
        Scene("Share the credential", "steps", "Step 2: share the credential", items=(
            "Credentials: pick the Claude one",
            "Sharing: choose the user or project",
            "They get Can use, nothing more",
        ), said=(
            ("Now the keys. In the left menu, open Credentials, and pick the one "
             "for Claude."),
            ("Open its Sharing tab. In the dropdown, choose the person or the "
             "project, and Save."),
            ("[emphasis] They get Can use. They can run workflows with it, but "
             "they can't see or edit its details. The secret stays hidden."))),
        Scene("", "pause", "Pause and try this", items=(
            "Share your workflow with a teammate",
            "Share one credential, and ask them to run it",
        ), said=(
            ("Pause the video and share the workflow with a teammate. Then share "
             "just one credential, so you can see the difference."),
            ("Ask them to run it, and then to try opening the credential. They "
             "should see no secret at all, only that they can use it."))),
        Scene("When a credential isn't shared", "bullets", "Credential not shared yet?",
              items=(
                  "They can view and run the workflow",
                  "They can edit the other nodes",
                  "Nodes using that credential stay locked",
              ), said=(
                  ("What if you share the workflow, but forget the credential? Your "
                   "teammate can still open it, look around, and run it, as the docs "
                   "describe."),
                  ("They can edit every node that doesn't use that credential. The "
                   "form, the Slack alert, the log, all fine."),
                  ("But they can't edit the Claude step. That's a handy sign. If "
                   "someone says a node is greyed out, share the credential, "
                   "and tell them to refresh."))),
        Scene("Share the Claude Code setup", "code", "Step 3: one file, in the repo",
              code=MCP_FILE, marks=((0, 1), (2, 5), (3, 4)),
              caption="the address comes from each person's environment", said=(
                  ("Teammates who use Claude Code need the same setup. Claude Code "
                   "can store a server in a file called dot mcp dot json."),
                  ("Commit it to your repository, and everyone gets the server. "
                   "Each person approves it the first time they start Claude Code "
                   "there, so nothing runs without a yes."),
                  ("See the dollar sign? That reads the address from each person's "
                   "own environment. Keep keys out of the file, always."))),
        Scene("Share the skill too", "bullets", "The reply skill, for everyone", items=(
            "It lives in .claude/skills",
            "Commit the folder to the repo",
            "Same reply style for the whole team",
        ), said=(
            ("Remember the reply skill from earlier? A project skill lives in a "
             "folder inside dot claude, slash skills."),
            ("Commit that folder, and your whole team gets the same skill, "
             "the next time they update the repository."),
            ("[confident] So every draft follows the same rules, no matter who "
             "asks, and you fix a rule in one place."))),
        Scene("", "quiz", "Where do your API keys go?", items=(
            "In dot mcp dot json", "In each person's environment",
        ), answer=1, caption="Files get shared. Environments don't.", said=(
            ("Last quiz. Where do the keys go, so a shared file never holds a "
             "secret? Comment."),
            ("B, each person's own environment. The file only points to it, and "
             "files get shared."))),
        Scene("Recap", "checklist", "Recap", items=(
            "Roles: viewer, editor, admin",
            "Workflow: Share, or add to the project",
            "Credential: Can use, secret hidden",
            "Repo: mcp file and skill, no keys",
        ), said=(
            "Recap. Give people the least role that does the job.",
            "Share the workflow, or add them to its project.",
            "Share credentials as Can use, and the secret stays hidden.",
            ("[happy] And put the shared setup in the repo, with no keys in it. "
             "Now the whole team can use the assistant."))),
    ),
)
