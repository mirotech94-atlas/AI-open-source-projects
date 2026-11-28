"""Approved by Miro 2026-11-24 (he authorised the review); facts re-read that day.

Study note: docs/academy-references/project_08.md

Project #8 (new entry, plan used up): watching our assistant after it is live,
with the executions list, careful retries and a failure on purpose.

Written 2026-11-24 as a draft. No live demo: the picture is a diagram, steps,
bullets, a log on screen, two quizzes and a pause.

Every product fact was read on the official n8n docs on 2026-11-24:

  docs.n8n.io/build/understand-workflows/understand-executions/
  view-all-executions.md
      (Overview > Executions tab shows "all executions from the workflows you
      have access to"; Filters: workflow, status Failed, Running, Success or
      Waiting, execution start; Retry execution, with the currently saved
      workflow or the original workflow; retries resume from the saved state;
      the failed node reprocesses all its input items, so check the target app
      for duplicates; each retry creates a new execution; a succeeded
      execution can't be retried; you can retry only while n8n still stores
      the execution, and failed ones aren't saved if "Save failed production
      executions" is "Do not save"; deleting a workflow deletes its history)
  docs.n8n.io/build/understand-workflows/understand-executions/
  view-executions-for-a-single-workflow.md
      (the workflow's own Executions tab)
  docs.n8n.io/build/flow-logic/handle-errors-gracefully.md
      (error workflow set under Options > Settings > Error workflow; it must
      start with the Error Trigger; one error workflow can serve several
      workflows; Stop and Error forces a failure; execution.id and
      execution.url are missing when the trigger node itself fails)
  docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.stopanderror.md
      (Error Type: Error Message or Error Object; pair it with the Error
      Trigger)
  docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.errortrigger.md
      (the workflow with the Error Trigger must be published; error workflows
      can't be tested by running manually)

The assistant (form, Claude draft, Sheets log, Slack alert, error workflow)
is the one the series built in project #1 to #7. Project #5 set the error
workflow up; this episode is about reading what happens after.
"""
from __future__ import annotations

from ..tutorial_series import (
    Episode,
    Scene,
    payoff_scene,
    plan,
    scripted,
)

# The hashtags are the first three of SERIES_HASHTAGS['project'], written out.
PLANNED = plan(
    8, 3, "project-watch-and-retry",
    "Watch Your Claude + n8n Assistant: Executions, Retries and Failure Alerts",
    "Watch it\nafter launch",
    ("Read the executions list, retry a failed run without double work, and make "
     "the assistant fail loudly on purpose, so the error workflow tells you."),
    ("n8n executions", "n8n retry failed execution", "n8n error workflow",
     "n8n stop and error"),
    ("#claudeai", "#mcp", "#n8n"),
    ("What a live assistant needs: look, retry, alert",
     "Reading the Executions list and its filters",
     "Retrying a failed run without duplicates",
     "Failing on purpose with Stop and Error",
     "When the alert has no link",
     "Recap checklist"))

LOG = """status   workflow       when
Success  Assistant      09:12
Failed   Assistant      09:40  Google Sheets
Success  Assistant      10:05
Success  Error alerts   09:40"""

PICTURE = (
    "Executions list\nsee every run",
    "Careful retry\nno double rows",
    "Failure on purpose\nthe alert fires",
)

EPISODE: Episode = scripted(
    PLANNED,
    title_hook="n8n Executions",
    title_benefit="Retry and Alerts",
    result="A watched assistant",
    links=(("View all executions",
            ("https://docs.n8n.io/build/understand-workflows/understand-executions/"
             "view-all-executions/")),
           ("Handle errors gracefully",
            "https://docs.n8n.io/build/flow-logic/handle-errors-gracefully/"),
           ("Stop and Error node",
            ("https://docs.n8n.io/integrations/builtin/core-nodes/"
             "n8n-nodes-base.stopanderror/")),
           ("Error Trigger node",
            ("https://docs.n8n.io/integrations/builtin/core-nodes/"
             "n8n-nodes-base.errortrigger/"))),
    ask="What's the first thing you check when an automation seems quiet?",
    topic_said=("Today: our assistant is live, so we learn to read its runs, retry "
                "a failed one safely, and make it fail loudly."),
    topic_shown="Watch it after launch",
    payoff=payoff_scene(
        ("By the end, you can find any failed run in seconds, retry it without "
         "doubling work, and trust the alert."),
        "Slack: 'Assistant failed: Claude reply was empty · open the run'",
        items=PICTURE,
        caption="you'll know first"),
    hook=Scene("What we'll do", "hook", "Know before the customer does", items=PICTURE,
               code=LOG, caption="you'll know first", said=(
                   ("Going live isn't the finish line. [curious] A quiet assistant "
                    "might be fine, or it might be broken. How would you know?"),
                   ("All you need is the assistant from the earlier parts and its "
                    "error workflow. Nothing new to install."))),
    needs=("the assistant workflow from the earlier episodes",
           "its error workflow from project five",
           "n8n, Cloud or self-hosted"),
    scenes=(
        Scene("Look, retry, alert", "diagram", "Three jobs after launch", items=(
            "Look\nthe Executions list",
            "Retry\na failed run, carefully",
            "Alert\nfail loudly on purpose",
        ), said=(
            ("After launch there are three jobs. First, look. n8n keeps a list of "
             "every run."),
            ("Second, retry. When one fails, you can run it again, but with care."),
            ("Third, alert. Make sure a real problem becomes a message to you, "
             "instead of a silence."))),
        Scene("Read the Executions list", "steps", "Step 1: read the list", items=(
            "Overview, then the Executions tab",
            "Filters: Status Failed",
            "Open the run, find the node that failed",
        ), said=(
            ("Open Overview, then the Executions tab. It lists runs from every "
             "workflow you can access. Inside one workflow, it has its own "
             "Executions tab too."),
            ("Select Filters, and set the status to Failed. You can also filter by "
             "workflow, and by when the run started."),
            ("Open a failed run and look for the node that failed. That's your "
             "first clue. Don't guess before you've looked."))),
        Scene("Retry a failed run", "steps", "Step 2: retry, carefully", items=(
            "Run options: Retry execution",
            "Saved workflow, or the original",
            "Check the target for duplicates",
        ), said=(
            ("Fixed the problem? Open that run's options and choose Retry "
             "execution. It continues from where it stopped, so finished nodes "
             "aren't redone."),
            ("Pick the currently saved workflow, to use your fix. Or pick the "
             "original, to run it exactly as it was. Each retry makes a new run "
             "in the list."),
            ("[emphasis] One catch. The failed node handles all its input items "
             "again, even ones it finished. So check the Sheet for double rows."))),
        Scene("", "quiz", "A failed node already handled some items. A retry risks…",
              items=("Nothing", "Doing some of them twice"), answer=1,
              caption="Check for duplicates after a retry.", said=(
                  ("Quick quiz. The Sheets node failed halfway. What does a retry "
                   "risk? Comment your answer."),
                  "B. It can do some items twice, so check for duplicates.")),
        Scene("", "subscribe", "Building along?", said=(
            "If you're building this assistant with me, subscribe for the last part.",)),
        Scene("Fail on purpose", "steps", "Step 3: Stop and Error", items=(
            "If: is Claude's draft empty?",
            "Stop and Error: Error Message",
            "The error workflow sends the alert",
        ), said=(
            ("Some bad runs don't fail by themselves. Claude might send back an "
             "empty draft. Add an If node after Claude, and check for that."),
            ("On the empty side, add Stop and Error. Type a clear message, like "
             "Claude reply was empty. That makes the run fail."),
            ("And a failed run starts your error workflow. Slack tells you which "
             "workflow, which node, and the message you wrote."))),
        Scene("", "pause", "Pause and try this", items=(
            "Open Executions and filter Failed",
            "Read the last failed run",
        ), said=(
            ("Pause the video and open your Executions list. Filter by Failed."),
            ("Open the latest failed run, and say out loud which node it was. "
             "No failed runs? That's a good problem to have."))),
        Scene("Keep the history", "bullets", "What n8n keeps for you", items=(
            "Failed runs: check Save failed production executions",
            "A retry works only on a stored run",
            "Delete a workflow, lose its history",
        ), said=(
            ("Your history is only useful if n8n keeps it. In the workflow's "
             "settings, check Save failed production executions. If it says Do not "
             "save, the failed runs vanish."),
            ("And a retry only works on a run that's still stored. No stored run, "
             "nothing to retry."),
            ("One more trap. Deleting a workflow deletes its run history too. So "
             "before you tidy up, look at what you'd lose."))),
        Scene("When the alert has no link", "bullets", "No run link in the alert?", items=(
            "The trigger itself failed",
            "No run id, no run url",
            "Alert with the workflow name instead",
        ), said=(
            ("[curious] Sometimes the alert arrives with no link to a run."),
            ("That happens when the trigger node itself fails. The workflow never "
             "ran, so there's no run id and no run link."),
            ("So put the workflow name in the alert too. And remember, the error "
             "workflow must be published, and it can't be tested by a manual run."))),
        Scene("", "quiz", "Failed runs are set to Do not save. Can you retry one?",
              items=("Yes", "No"), answer=1,
              caption="You can only retry what n8n still stores.", said=(
                  ("Last quiz. The workflow's setting says Do not save for failed "
                   "production runs. Can you retry one? Comment."),
                  "B, no. You can only retry a run while n8n still stores it.")),
        Scene("Recap", "checklist", "Recap", items=(
            "Executions tab: filter Failed",
            "Retry from the run's options",
            "Stop and Error for quiet failures",
            "Error workflow: published, name in the alert",
        ), said=(
            "Recap. The Executions tab lists every run, and Failed finds the bad ones.",
            "Retry from the run's options, then check for duplicates.",
            "Use Stop and Error, so a quiet failure becomes a loud one.",
            ("[happy] And keep the error workflow published, with the workflow's "
             "name in the alert. Now you'll know first."))),
    ),
)
