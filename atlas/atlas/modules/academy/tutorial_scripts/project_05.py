"""Project #5: testing our assistant, handling errors, and going live.

Study note: docs/academy-references/project_05.md

Written 2026-10-18 by the script writer (docs/academy-script-writer.md). No
live demo: no new recording can be made, so the picture is a test plan on
screen, steps, bullets, a diagram, two quizzes and a pause.

Every product fact was read on the official n8n docs on 2026-10-18:

  docs.n8n.io/build/work-with-data/pin-and-mock-data.md
      (mock data "using either the Code node or the Edit Fields (Set)
      node"; "The Edit Fields node is a good choice for small tests";
      pinning: "saving the output data of a node and using the saved data
      instead of fetching fresh data"; with a webhook trigger "you don't
      need to use the external system every time you test"; "Data pinning
      isn't available for production workflow executions")
  docs.n8n.io/build/flow-logic/handle-errors-gracefully.md
      ("For each workflow, you can set an error workflow in Workflow
      Settings. It runs if an execution fails"; "send email or Slack alerts";
      "The error workflow must start with the Error Trigger"; "You can use
      the same error workflow for multiple workflows"; the error data:
      execution.url, execution.error.message, execution.lastNodeExecuted,
      workflow.name)
  docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.errortrigger.md
      ("If a workflow uses the Error Trigger node, you don't have to publish
      the workflow"; "You can't test error workflows when running workflows
      manually. The Error Trigger only runs when an automatic workflow
      errors")
  docs.n8n.io/build/understand-workflows/save-and-publish-workflows.md
      ("n8n auto saves your workflow while you're editing"; "All edits
      remain in draft until you publish"; "Production executions will use
      this published version, not your latest edits"; "Webhook and form
      triggers will use their production URLs"; "Published, partial ... at
      least one trigger failed to activate ... Hover over the button to see
      which triggers failed, then publish again to retry them")
  docs.n8n.io/build/understand-workflows/understand-executions/view-executions-for-a-single-workflow.md
      ("In the workflow, select the Executions tab"; filters)
  docs.n8n.io/build/understand-workflows/understand-executions/debug-executions.md
      (failed executions: "select Debug in editor"; "n8n copies the
      execution data into your current workflow, and pins the data in the
      first node"; available on n8n Cloud and on registered self-hosted
      editions, so the script says "where your plan has it")

The test cases are our own, for the form, Claude draft, Sheets log and
Slack alert assistant this series built (project #1 to #4).
"""
from __future__ import annotations

from ..tutorial_series import (
    PROJECT_PLANNED,
    Episode,
    Scene,
    payoff_scene,
    scripted,
)

TEST_PLAN = """1. Happy path: a normal question            → draft, row, Slack alert
2. Empty message                              → stopped, you're told
3. Odd input: a very long paste               → still one short draft
4. A step fails: Sheets credential removed    → error workflow alerts you"""

PICTURE = (
    "Four test runs\none good, three broken",
    "An error workflow\nwatching it",
    "Published\nand checked daily",
)

EPISODE: Episode = scripted(
    next(e for e in PROJECT_PLANNED if e.number == 5),
    title_hook="Testing and Going Live",
    title_benefit="Error Alerts",
    links=(("Pin and mock data",
            "https://docs.n8n.io/build/work-with-data/pin-and-mock-data/"),
           ("Handle errors gracefully",
            "https://docs.n8n.io/build/flow-logic/handle-errors-gracefully/"),
           ("Save and publish workflows",
            "https://docs.n8n.io/build/understand-workflows/save-and-publish-workflows/"),
           ("Debug executions",
            ("https://docs.n8n.io/build/understand-workflows/understand-executions/"
             "debug-executions/"))),
    ask="What's the strangest message a customer has ever sent you? Tell me below.",
    topic_said=("Today: we test our assistant on purpose, catch its errors, publish it, "
                "and watch its first real runs."),
    topic_shown="Test, catch, publish, watch",
    payoff=payoff_scene(
        ("By the end, our assistant is live, it's been broken on purpose four ways, "
         "and if it ever fails, you'll hear about it first."),
        "Slack: 'Assistant failed at Google Sheets · open the execution'",
        items=PICTURE,
        caption="live, and watched"),
    hook=Scene("What we'll do", "hook", "Ready for real customers", items=PICTURE,
               code=TEST_PLAN, caption="live, and watched", said=(
                   ("Our assistant works on our test message. [curious] But what about "
                    "the messages real people send?"),
                   ("All you need is the workflow from the earlier parts, and n8n. "
                    "Nothing new to install."))),
    needs=("the assistant workflow from the earlier episodes", "n8n, Cloud or self-hosted"),
    scenes=(
        Scene("A test plan", "code", "Four tests, on purpose", code=TEST_PLAN,
              marks=((0, 1), (1, 2), (2, 3), (3, 4)),
              caption="one happy path, three broken ones", said=(
                  ("Here's the plan. Test one is the happy path: a normal question. "
                   "Expect a draft, a new row, and a Slack alert."),
                  ("Test two, an empty message. The assistant should stop, and tell "
                   "you, not draft a reply to nothing. If it doesn't, that's a fix to "
                   "make before going live."),
                  ("Test three, something odd: a huge paste. The draft should still "
                   "be short, because our skill says so."),
                  ("And test four, a step that fails. We'll break the Google Sheets "
                   "step on purpose, and see who notices."))),
        Scene("Test without the form", "steps", "Test with mock data", items=(
            "Edit Fields: a fake form entry",
            "Pin it: reuse it for every test",
            "Pinned data never runs in production",
        ), said=(
            ("You don't need to fill in the form again and again. Use an Edit Fields node "
             "to make a fake entry, a name and a question."),
            ("Or pin the form's real output once. Pinning saves a node's output, so "
             "every test reuses the same data."),
            ("[emphasis] And don't worry about leaving it in: pinned data is only "
             "used while you build, never in production."))),
        Scene("", "quiz", "Test two: the message is empty. Pass means…", items=(
            "Claude drafts something anyway", "It stops and you're told",
            "The row is deleted",
        ), answer=1, caption="A good failure is a loud one.", said=(
            ("Quick quiz. Test two sends an empty message. What does a pass look "
             "like? Comment your answer."),
            "B. It stops, and you're told. A good failure is a loud one.")),
        Scene("Catch what breaks", "diagram", "An error workflow", items=(
            "Assistant fails\nin a real run",
            "Error Trigger\nwhat, where, which run",
            "Slack alert\nwith a link to it",
        ), said=(
            ("For test four, the assistant needs an error workflow. A separate "
             "workflow that starts with the Error Trigger."),
            ("When a run fails, it gets the details: the workflow's name, the last "
             "node that ran, the error, and a link to that run."),
            ("Send those to Slack. One error workflow can watch all your workflows, "
             "so build it once. And the error workflow itself doesn't need "
             "publishing."))),
        Scene("", "subscribe", "Building along?", said=(
            "If you're building this assistant with me, subscribe for the last parts.",)),
        Scene("Go live", "steps", "Publish it", items=(
            "Settings → Error workflow → pick it",
            "Publish: the form's production URL is live",
            "Partial? Hover the button, publish again",
        ), said=(
            ("Open the assistant's settings, and set the error workflow to the one we "
             "just made. Save, and the two are linked."),
            ("Now publish. Until you do, every edit is a draft. Once published, the "
             "form uses its production address, and real runs use this version."),
            ("If the button says published, partial, one trigger didn't start. Hover "
             "over it to see which, fix it, and publish again."))),
        Scene("", "pause", "Pause and try this", items=(
            "Run your four tests",
            "Then publish the assistant",
        ), said=(
            ("Pause the video and run your four tests. Write down what each one did. "
             "Remember, test four's alert only comes from a real run, not a test "
             "click."),
            ("When all four behave the way you wrote down, publish the assistant, "
             "and resume."))),
        Scene("Watch the first runs", "steps", "Watch the first real runs", items=(
            "Open the Executions tab",
            "A red run? Debug in editor",
            "Fix it, re-run with the same data",
        ), said=(
            ("For the first week, open the Executions tab every day. Every real run "
             "is there, good or bad. Use the filters to show only the failed "
             "ones."),
            ("[sighing] Found a red one? Choose Debug in editor, where your plan has "
             "it. n8n copies that run's data into the editor."),
            ("Fix the step, and run it again with exactly the data that failed. No "
             "waiting for it to happen twice."))),
        Scene("", "quiz", "You edit a published workflow. Do real runs change?", items=(
            "Yes, straight away", "No, not until you publish again",
        ), answer=1, caption="Real runs use the published version.", said=(
            ("Last quiz. You tweak the published assistant. Do real runs use your "
             "change right away? Comment."),
            ("B, no. Edits stay a draft. Real runs use the published version until "
             "you publish again."))),
        Scene("Recap", "checklist", "Recap", items=(
            "Test one happy path, three broken ones",
            "Mock or pin data while you test",
            "An error workflow tells you in Slack",
            "Publish, then read Executions daily",
        ), said=(
            "Recap. We tested one happy path, and three ways to break it.",
            "Mock or pinned data made testing quick, and never reaches production.",
            "An error workflow tells us in Slack, with a link to the failed run.",
            ("[happy] And we published, then watched the first runs. Our assistant "
             "is live, and you'll know if it trips."))),
    ),
)
