"""Project #6: email as a second way to reach our assistant.

Study note: docs/academy-references/project_06.md

Written 2026-10-18 by the script writer (docs/academy-script-writer.md). No
live demo: no new recording can be made, so the picture is a diagram,
steps, bullets, the log sheet on screen, two quizzes and a pause.

Every product fact was read on the official n8n docs on 2026-10-18:

  docs.n8n.io/integrations/builtin/trigger-nodes/n8n-nodes-base.gmailtrigger.md
      (Message Received: "The node triggers for new messages at the selected
      Poll Time"; Simplify returns "email message IDs, labels, and email
      headers, including: From, To, CC, BCC, and Subject"; Read Status
      "Unread emails only (default)"; the Search filter, such as from:)
  .../n8n-nodes-base.gmailtrigger/common-issues.md
      (401 unauthorized_client: "an issue with the credential you're using
      and its scopes or permissions"; for OAuth2 "make sure you've enabled
      the Gmail API in APIs & Services > Library")
  docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.gmail/message-operations.md
      (Reply: "send a message as a reply to an existing message", Message
      ID, Message; "Append n8n attribution: By default, the node appends the
      statement This email was sent automatically with n8n ... To remove
      this statement, turn this option off")
  docs.n8n.io/build/flow-logic/convert-to-sub-workflows.md
      ("refactor your workflows into reusable parts"; "Select the desired
      nodes on the canvas. Right-click the canvas background and select
      Convert to sub-workflow"; the selection "Must not include trigger
      nodes")
  docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.executeworkflow.md
      ("Use the Execute Sub-workflow node to run a different workflow";
      Source: Database, From list; Workflow Inputs)
  docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.slack/approvals.md
      (Message > Send and Wait for Response, Response Type Approval, output
      approved; Restrict Who Can Approve)
  docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.if.md
      (split a workflow on a comparison; the true output)
  docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.googlesheets/sheet-operations.md
      (Append Row "Create a new row")

The sheet columns are our own, for the assistant this series built: the
form, the Claude draft, the Sheets log and the Slack alert (project #1 to
#5).
"""
from __future__ import annotations

from ..tutorial_series import (
    PROJECT_PLANNED,
    Episode,
    Scene,
    payoff_scene,
    scripted,
)

LOG = """time              channel  from                 status
2026-11-03 09:12  form     ana@example.com      replied
2026-11-03 09:40  email    sam@example.com      approved, replied
2026-11-03 10:05  email    lee@example.com      declined"""

PICTURE = (
    "Form or email\ntwo ways in",
    "One Claude step\nshared by both",
    "Your approval\nthen the reply",
)

EPISODE: Episode = scripted(
    next(e for e in PROJECT_PLANNED if e.number == 6),
    title_hook="n8n Email Channel",
    title_benefit="Two Ways In",
    result="An email channel",
    tags=("n8n gmail trigger", "n8n execute sub-workflow", "n8n gmail reply",
          "advanced", "ATLAS Academy"),
    links=(("Gmail Trigger",
            ("https://docs.n8n.io/integrations/builtin/trigger-nodes/"
             "n8n-nodes-base.gmailtrigger/")),
           ("Gmail message operations",
            ("https://docs.n8n.io/integrations/builtin/app-nodes/"
             "n8n-nodes-base.gmail/message-operations/")),
           ("Convert to sub-workflows",
            "https://docs.n8n.io/build/flow-logic/convert-to-sub-workflows/"),
           ("Slack approvals",
            ("https://docs.n8n.io/integrations/builtin/app-nodes/"
             "n8n-nodes-base.slack/approvals/"))),
    ask="How do most of your customers reach you: a form, email or something else?",
    topic_said=("Today: our assistant answers email too, with the same Claude step, "
                "and nothing sent until you approve it."),
    topic_shown="A second way in: email",
    payoff=payoff_scene(
        ("By the end, a customer can simply email you, and the same assistant drafts "
         "the answer, waits for your yes, and logs it."),
        "Slack: 'Email from sam@example.com · draft ready' · Approve · Deny",
        items=PICTURE,
        caption="two ways in, one set of rules"),
    hook=Scene("What we'll build", "hook", "Answer email too", items=PICTURE,
               code=LOG, caption="two ways in, one set of rules", said=(
                   ("Not every customer fills in a form. [curious] Plenty of them just "
                    "send an email. So let's meet them there."),
                   ("All you need is the assistant from the earlier parts, a Gmail "
                    "account, and the Slack app we already use for alerts."))),
    needs=("the assistant workflow from the earlier episodes", "a Gmail account",
           "the Slack app from the earlier episodes"),
    scenes=(
        Scene("The plan", "diagram", "Two ways in, one brain", items=(
            "Form Trigger\nor Gmail Trigger",
            "One shared sub-workflow\nClaude drafts",
            "Approve in Slack\nthen reply and log",
        ), said=(
            ("Here's the plan. The form keeps working, and a Gmail Trigger becomes a "
             "second way in."),
            ("Both send the message to one shared piece: the Claude step, moved into "
             "a sub-workflow. Fix it once, and both channels get the fix."),
            ("Then a person approves in Slack, and only then does the reply go out, "
             "and land in the log."))),
        Scene("Step 1: share the Claude step", "steps", "Step 1: share the Claude step",
              items=(
                  "Select the Claude nodes, not the trigger",
                  "Right-click: Convert to sub-workflow",
                  "Call it with Execute Sub-workflow",
              ), said=(
                  ("First, open the assistant and select the nodes that draft the "
                   "reply. Leave the form trigger out. Triggers can't go in."),
                  ("Right-click the canvas background, and choose Convert to "
                   "sub-workflow. n8n moves them into a reusable workflow of their "
                   "own."),
                  ("Your form workflow now calls it with an Execute Sub-workflow node. "
                   "[confident] Same rules, in one place. Run the form once more, to "
                   "be sure nothing changed."))),
        Scene("Step 2: the Gmail Trigger", "steps", "Step 2: the Gmail Trigger", items=(
            "Gmail Trigger: Message Received",
            "Simplify off · unread · a filter",
            "Execute Sub-workflow: the same Claude step",
        ), said=(
            ("Now a new workflow, starting with a Gmail Trigger. It checks for new "
             "messages on the schedule you pick. Turn Simplify off: on, it only "
             "returns the headers and labels, not the whole message."),
            ("Keep it on unread emails only, and add a search filter, so it only picks "
             "up mail sent to your shop's address."),
            ("Then an Execute Sub-workflow node, pointing at the shared Claude step. "
             "Map the email into its inputs, starting with who it's from and the "
             "subject."))),
        Scene("", "quiz", "You change Claude's rules. How many places to edit?", items=(
            "Two: one per channel", "One: the shared sub-workflow", "None",
        ), answer=1, caption="One sub-workflow, used by both channels.", said=(
            ("Quick quiz. You want Claude to sound warmer. How many places do you "
             "edit now? Comment your answer."),
            "B, one. Both channels call the same sub-workflow.")),
        Scene("", "subscribe", "Building along?", said=(
            "If you're building this assistant with me, subscribe for the last part.",)),
        Scene("Step 3: approve, then reply", "steps", "Step 3: approve, then reply",
              items=(
                  "Slack: Send and Wait, Approval",
                  "If: approved is true",
                  "Gmail: Reply · attribution off",
              ), said=(
                  ("Next, a Slack node with Send and Wait for Response, set to "
                   "Approval. Put the email and Claude's draft in the message, and "
                   "restrict approval to you."),
                  ("An If node checks approved is true. Nothing else goes any "
                   "further. A declined draft stops right here, and you can write "
                   "that reply yourself."),
                  ("Then Gmail, Reply, using the original message's ID. Turn off the "
                   "n8n attribution option, or every reply ends with a line saying it "
                   "was sent automatically."))),
        Scene("", "pause", "Pause and try this", items=(
            "Email yourself a test question",
            "Approve the draft in Slack",
        ), said=(
            ("Pause the video and send yourself a test email, asking if you're open on "
             "Saturday."),
            ("Wait for the trigger to pick it up, approve the draft in Slack, and "
             "resume when the reply lands."))),
        Scene("Log both channels in Google Sheets", "steps", "One log for both", items=(
            "Google Sheets: Append Row, in both",
            "A channel column: form or email",
            "Declined drafts are logged too",
        ), said=(
            ("Last step: log it. Add Google Sheets, Append Row, to both workflows."),
            ("Give it a channel column, form or email, and use the same columns in "
             "both, so the sheet stays tidy."),
            ("Now one sheet shows every request, which way it came in, and what "
             "happened. Declined ones too. [emphasis] Nothing disappears."))),
        Scene("When Gmail says no", "bullets", "Gmail says 401?", items=(
            "401 unauthorized_client",
            "Turn on the Gmail API for your Google project",
            "Then test the trigger again",
        ), said=(
            ("[sighing] A common first error: the Gmail Trigger fails with a 401, "
             "unauthorized client."),
            ("It's the credential's permissions. For an OAuth credential, open your "
             "Google project, go to APIs and Services, then Library, and turn on the "
             "Gmail API."),
            "Then test the trigger again, and the 401 should be gone.")),
        Scene("", "quiz", "Nobody approves the email draft. What's sent?", items=(
            "The draft, after a while", "Nothing",
        ), answer=1, caption="Only an approved draft reaches Gmail Reply.", said=(
            ("Last quiz. The draft sits in Slack, and nobody clicks. What does the "
             "customer get? Comment."),
            ("B, nothing yet. Only an approved draft reaches the Reply node. So check "
             "Slack often."))),
        Scene("Recap", "checklist", "Recap", items=(
            "Claude step: one shared sub-workflow",
            "Gmail Trigger: unread, filtered",
            "Slack approval, then Gmail Reply",
            "One sheet logs both channels",
        ), said=(
            "Recap. The Claude step now lives in one shared sub-workflow.",
            "A Gmail Trigger brings in unread, filtered emails.",
            "You approve in Slack, then Gmail replies, with no automatic footer.",
            ("[happy] And one sheet logs both channels. Two ways in, one assistant, "
             "and you still decide what goes out."))),
    ),
)
