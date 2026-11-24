"""n8n #3: looking after your workflows once they're running.

Written 2026-10-16, the first episode of the scheduled script writer's
package (docs/academy-script-writer.md). No live demo: no new recording can
be made, so the picture is steps, a diagram, two quizzes and a lab.

Every product fact was read on the official n8n docs on 2026-10-16:

  docs.n8n.io/build/understand-workflows/understand-executions.md
      ("An execution is a single run of a workflow"; the workflow-level list
      and the all-executions list)
  docs.n8n.io/build/understand-workflows/understand-executions/types-of-executions.md
      (manual executions run when you select Execute workflow; production
      executions run when the workflow is published and a trigger fires; on
      paid plans production executions count towards the quota)
  docs.n8n.io/build/understand-workflows/understand-executions/view-executions-for-a-single-workflow.md
      (the Executions tab in the top menu; Filters: Status -- Failed,
      Running, Success, Waiting -- and Execution start; retry a failed run
      with the Refresh icon: "Retry with currently saved workflow" or
      "Retry with original workflow")
  docs.n8n.io/build/understand-workflows/understand-executions/debug-executions.md
      ("Debug in editor" for a failed run, "Copy to editor" for a
      successful one; the data is pinned to the first node; "Feature
      availability": all n8n Cloud plans, self-hosted Registered Community,
      Business and Enterprise -- said in the Retry scene)
  docs.n8n.io/build/understand-workflows/save-and-publish-workflows.md
      (autosave "typically within 1 to 5 seconds"; edits stay in draft until
      you publish; the Publish button, Shift + P; "Published, has changes":
      production runs the published version until you publish again;
      unpublish from the dropdown next to Publish, Cmd/Ctrl + U)
  docs.n8n.io/build/manage-workflows/view-change-history.md
      (workflow history is earlier versions, not runs; the Workflow history
      icon; Options: Restore version, Clone to new workflow; "Versions from
      the last 24 hours are available for all users", longer by plan)
  docs.n8n.io/build/manage-workflows/tag-workflows.md
      (+ Add tag; Workflows page, Filters, Tags; "Tags are global. This
      means when you create a tag, it's available to all users on your n8n
      instance" -- said as "shared across the whole instance")
  docs.n8n.io/build/manage-workflows/configure-workflow-settings.md
      (Workflow menu, Settings; "Save manual executions": whether n8n saves
      executions started by the user in the editor)
  docs.n8n.io/deploy/host-n8n/configure-n8n/basic-configuration/use-environment-variables/executions.md
      (EXECUTIONS_DATA_SAVE_MANUAL_EXECUTIONS defaults to true)

The planned outline named folders; no official page describing workflow
folders in the editor was found on 2026-10-16 (only the API and packages
mention them), so this episode teaches tags instead and its summary, title
and tags say so. The lab uses only the Manual Trigger and Edit Fields (Set
v3.4), as the earlier labs do.
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
  "name": "ATLAS Academy lab: run history",
  "nodes": [
    { "name": "Start", "type": "n8n-nodes-base.manualTrigger",
      "typeVersion": 1, "position": [0, 0], "parameters": {} },
    { "name": "Edit Fields", "type": "n8n-nodes-base.set",
      "typeVersion": 3.4, "position": [240, 0],
      "parameters": { "assignments": { "assignments": [
          { "id": "lab3-status", "name": "status",
            "value": "=Version 1 ran at {{ $now.toFormat('HH:mm') }}",
            "type": "string" } ] },
        "options": {} } }
  ],
  "connections": {
    "Start": { "main": [[{ "node": "Edit Fields", "type": "main", "index": 0 }]] }
  }
}"""

LAB_FILE = "n8n-run-history.json"
LAB_FILES: dict[str, str] = {LAB_FILE: LAB_JSON + "\n"}

PICTURE = (
    "Executions\nevery run, listed",
    "Publish\nwhat runs for real",
    "History\nearlier versions",
)

EPISODE: Episode = scripted(
    next(e for e in N8N_PLANNED if e.number == 3),
    title="Managing n8n Workflows: Executions, Retries, Publish, History and Tags",
    summary=("Look after n8n workflows once they run: read the Executions list, retry "
             "a failed run, publish and unpublish safely, bring back an earlier "
             "version from workflow history, and label workflows with tags."),
    tags=("n8n", "n8n tutorial", "n8n tutorial for beginners 2026",
          "n8n workflow tutorial step by step", "n8n executions",
          "n8n workflow history", "n8n retry failed execution", "n8n publish workflow",
          "n8n tags", "n8n tutorial for beginners", "beginner", "ATLAS Academy"),
    # docs/academy-youtube-plan.md rule 2: this lesson manages what was
    # built and builds nothing new, so it wears the Learn formula.
    title_hook="n8n Workflow Management",
    title_formula=2,
    links=(("View executions for a workflow",
            ("https://docs.n8n.io/build/understand-workflows/understand-executions/"
             "view-executions-for-a-single-workflow")),
           ("Save and publish workflows",
            "https://docs.n8n.io/build/understand-workflows/save-and-publish-workflows"),
           ("Workflow history",
            "https://docs.n8n.io/build/manage-workflows/view-change-history"),
           ("Tag workflows", "https://docs.n8n.io/build/manage-workflows/tag-workflows")),
    ask=("Have you ever needed an old version of something back? Tell me in the "
         "comments."),
    topic_said=("Today: looking after your n8n workflows once they're running, "
                "runs, retries, publishing, history and tags."),
    topic_shown="Executions, publish, history and tags",
    payoff=payoff_scene(
        ("You'll end able to find any run of your workflow, retry a failed one, and "
         "bring back yesterday's version."),
        "Failed run → fixed → retried → Success",
        items=PICTURE,
        caption="your workflows, under control"),
    hook=Scene("What you'll learn", "hook", "Three places to look", items=PICTURE,
               code=("Executions → what ran, and how it went\n"
                     "Publish → what's live right now\n"
                     "History → undo a change"),
               caption="your workflows, under control", said=(
                   ("Building a workflow is half the job. The other half is looking "
                    "after it once it runs by itself."),
                   ("[confident] Three places do most of that work: the run list, the "
                    "publish button, and the history of your changes."),
                   ("All you need is n8n, Cloud or your own, and one workflow. And a "
                    "lab file waits at the end, so you can try it yourself."))),
    needs=("n8n, Cloud or self-hosted", "any workflow, or the free lab file"),
    scenes=(
        Scene("The Executions list", "steps", "Find every run", items=(
            "Open the workflow → Executions tab",
            "One row per run: its status and start time",
            "Filters: Failed, Running, Success, Waiting",
        ), said=(
            ("Let's start where you'll spend the most time. Open any workflow, and "
             "at the top, click the Executions tab."),
            ("Every row is one execution: one run of the workflow, whether it "
             "worked, and when it started."),
            ("Lots of runs? Click Filters, and show only the failed ones, or one "
             "time window."))),
        Scene("Manual or production", "bullets", "Two kinds of run", items=(
            "Manual: you click Execute workflow",
            "Production: published, started by its trigger",
            "On paid plans, production runs count",
        ), said=(
            ("A manual run is one you start, by clicking Execute workflow while you "
             "build."),
            ("A production run starts by itself. The workflow is published, and its "
             "trigger fires: a schedule, a webhook, or an app."),
            ("Why care? On paid plans, production runs count towards your limit. "
             "Test clicks don't."))),
        Scene("", "quiz", "You click Execute workflow. That run is…", items=(
            "Manual", "Production", "Not a run at all",
        ), answer=0, caption="You started it: a manual run.", said=(
            ("Quick quiz. You click Execute workflow. Is that run manual or "
             "production? Comment your answer."),
            "It's A, manual. You started it yourself.")),
        Scene("Retry a failed run", "steps", "When a run fails", items=(
            "Fix the workflow first",
            "Refresh icon on the failed run",
            "Retry with currently saved workflow",
            "Or Debug in editor: the old data, pinned",
        ), said=(
            ("[sighing] Sooner or later, a run fails. Open it, see which node went "
             "red, and fix that node."),
            ("Back in the Executions list, click the Refresh icon on the failed run."),
            ("Pick Retry with currently saved workflow. That runs your fixed version "
             "on the same data that failed. Retry with original workflow runs it "
             "again with no changes."),
            ("Or click Debug in editor: n8n pins that run's data to the first node, "
             "so you can test the fix. Self-hosted, that needs a registered Community "
             "edition or higher."))),
        Scene("", "subscribe", "Still with me?", said=(
            "If this is saving you a headache, subscribe.",)),
        Scene("Save and publish", "steps", "Draft or live?", items=(
            "Edits save by themselves in seconds",
            "They stay a draft until you publish",
            "Publish button, or Shift + P",
            "Unpublish: the arrow next to Publish",
        ), said=(
            ("Now, saving. There's no save button. n8n saves your edits by itself, "
             "usually within a few seconds."),
            ("[emphasis] But saved isn't live. Your edits stay a draft until you "
             "publish. A published workflow keeps running the last version you "
             "published."),
            ("When you're ready, click Publish, or press Shift and P. If the button "
             "says published, has changes, your newest edits aren't live yet."),
            ("And to stop a workflow from running by itself, open the arrow next to "
             "Publish and choose Unpublish."))),
        Scene("", "pause", "Pause and try this", items=(
            "Open a workflow and find Publish",
            "Open the arrow next to it",
        ), said=(
            ("Pause the video and try it. Open one of your workflows and find the "
             "Publish button,"),
            ("then open the little arrow next to it and read the options. Resume "
             "when you've had a look."))),
        Scene("Workflow history", "diagram", "Runs are not versions", items=(
            "Executions\nruns of the workflow",
            "Workflow history\nearlier versions",
            "Restore version\nback to how it was",
        ), said=(
            ("Here's a common mix-up. Executions are runs, not older versions of "
             "the workflow."),
            ("Those live in workflow history. Click the history icon at the top, and "
             "you'll see your saved versions, with a preview of each one."),
            ("Changed something and broke it? Open the version's menu and choose "
             "Restore version. [excited] Undo, for a whole workflow. The last 24 "
             "hours are there on every plan; longer depends on your plan."))),
        Scene("Tags and names", "bullets", "Find it again later", items=(
            "+ Add tag, next to the workflow's name",
            "Workflows page → Filters → Tags",
            "Tags are shared across your n8n",
        ), said=(
            ("Last one: tidiness. With ten workflows, finding the right one gets "
             "slow. Give each a clear name, and a tag."),
            ("Click Add tag next to the name, and pick or type one, like reports. "
             "Then filter by it on the Workflows page."),
            ("One thing to know: tags are shared across the whole instance. "
             "[chuckling] So pick names everyone will understand."))),
        Scene("", "quiz", "You want yesterday's version back. Where?", items=(
            "Executions", "Workflow history", "Tags",
        ), answer=1, caption="Versions live in workflow history.", said=(
            ("Last quiz. You want yesterday's version of a workflow back. Where do "
             "you go? Comment."),
            "B, workflow history. Executions are runs, not versions.")),
        lab_scene("Lab: run history", "Lab: run history", LAB_FILE,
                  do=f"Import {LAB_FILE} · run it twice",
                  expect="Executions tab: two runs, both Success",
                  stuck="No runs listed? Settings → Save manual executions",
                  challenge="Change it to Version 2, then restore Version 1",
                  said=(
                      ("Your lab. Download the lab file from the description, import "
                       "it, and click Execute workflow twice."),
                      ("Open the Executions tab. Expect two runs, both marked as a "
                       "success."),
                      ("Stuck? If no runs are listed, open the workflow menu, then "
                       "Settings, and switch Save manual executions on."),
                      ("Your challenge: change the text to version two, then use "
                       "workflow history to bring back version one."))),
        Scene("Recap", "checklist", "Recap", items=(
            "Executions: every run, with filters",
            "Retry or Debug in editor after a fix",
            "Saved is a draft · Publish makes it live",
            "History brings back versions · tags find workflows",
        ), said=(
            "Recap. The Executions tab lists every run, and filters find the failed ones.",
            "After a fix, retry the failed run, or debug it in the editor.",
            "Edits save by themselves, but only Publish makes them live.",
            ("[happy] And workflow history brings back an earlier version, while tags "
             "help you find your workflows."))),
    ),
)
