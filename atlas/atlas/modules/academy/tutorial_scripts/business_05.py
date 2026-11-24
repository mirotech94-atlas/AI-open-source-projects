"""Business #5: social posts with AI captions, scheduled, and approved by you.

Study note: docs/academy-references/business_05.md

Written 2026-10-18 by the script writer (docs/academy-script-writer.md). No
live demo: no new recording can be made, so the picture is steps, bullets,
a diagram, the content sheet on screen, two quizzes and a pause.

Every product fact was read on the official n8n docs on 2026-10-18:

  docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.scheduletrigger.md
      (Days interval, "Trigger at Hour"; "If a workflow uses the Schedule
      node as a trigger, make sure that you save and publish the workflow")
  docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.googlesheets.md
      and .../n8n-nodes-base.googlesheets/sheet-operations.md
      (Get Row(s) "Read all rows in a sheet"; Update Row "update existing
      row in a sheet")
  docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.filter.md
      ("If the item doesn't meet the condition, the Filter node omits the
      item from its output"; String "is equal to"; Date & Time "is equal
      to"; AND between conditions)
  docs.n8n.io/build/work-with-data/handle-special-data-types/work-with-dates-and-times.md
      ("today: a Luxon object containing the current timestamp, rounded
      down to the day")
  docs.n8n.io/integrations/builtin/cluster-nodes/root-nodes/n8n-nodes-langchain.chainllm.md
      (Basic LLM Chain: "set the prompt that the model will use"; Prompt
      "Define below" with "static text or an expression"; a System message
      "for things like defining tone"; "n8n ignores these options if you
      don't connect a chat model")
  docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.slack.md and
  .../n8n-nodes-base.slack/approvals.md
      (Message > "Send and Wait for Response: Send a message and wait for a
      response from the recipient before continuing"; Response Type
      Approval; output "approved"; Restrict Who Can Approve; "If you leave
      the approver list empty, anyone who can see the message can approve";
      "Your n8n instance to be reachable from Slack over public HTTPS ...
      an instance running on localhost won't work"; the Signing Secret:
      "Without it, the buttons render but clicks don't resume the
      workflow")
  docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.if.md
      ("split a workflow conditionally based on comparison operations";
      the true output)
  docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.linkedin.md
      (Post: Create; Post As Person or Organization; Text "the post
      contents")

No reach, engagement or posting-time claim is made. The sheet on screen is
example data. LinkedIn is the one social node shown because it's the one
whose post operation was read today.
"""
from __future__ import annotations

from ..tutorial_series import (
    BUSINESS_PLANNED,
    Episode,
    Scene,
    payoff_scene,
    scripted,
)

SHEET = """date        topic                          status
2026-11-03  New winter opening hours       ready
2026-11-05  Meet the team: our new baker   ready
2026-11-07  Behind the scenes, Saturday    draft"""

PICTURE = (
    "Your content sheet\ntoday's topic",
    "AI drafts\nthe caption",
    "You approve\nthen it posts",
)

EPISODE: Episode = scripted(
    next(e for e in BUSINESS_PLANNED if e.number == 5),
    title_hook="n8n AI Captions",
    title_benefit="You Approve Posts",
    result="Approved AI captions",
    links=(("Slack approvals",
            ("https://docs.n8n.io/integrations/builtin/app-nodes/"
             "n8n-nodes-base.slack/approvals/")),
           ("Basic LLM Chain",
            ("https://docs.n8n.io/integrations/builtin/cluster-nodes/root-nodes/"
             "n8n-nodes-langchain.chainllm/")),
           ("LinkedIn node",
            "https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.linkedin/"),
           ("Schedule Trigger",
            ("https://docs.n8n.io/integrations/builtin/core-nodes/"
             "n8n-nodes-base.scheduletrigger/"))),
    ask="What would you post about first? Tell me in the comments.",
    topic_said=("Today: AI writes the caption, you approve it in Slack, and the post "
                "goes out on the day you planned."),
    topic_shown="AI captions, your approval",
    payoff=payoff_scene(
        ("By the end, your posts are drafted for you each morning, and not one of "
         "them goes out until you click Approve."),
        "Slack: 'Caption ready: New winter opening hours' · Approve · Decline",
        items=PICTURE,
        caption="nothing posts without your yes"),
    hook=Scene("What we'll build", "hook", "Posts that wait for you", items=PICTURE,
               code=SHEET, caption="nothing posts without your yes", said=(
                   ("[sighing] Posting every week sounds easy, until it's Thursday and "
                    "you haven't written a thing."),
                   ("So AI writes the first draft, and you stay the editor. All you need "
                    "is n8n, a Google Sheet, Slack, an AI model and a LinkedIn account."))),
    needs=("n8n, reachable over public HTTPS for Slack approvals", "a Google Sheet",
           "a Slack app", "a chat model credential", "a LinkedIn account"),
    scenes=(
        Scene("The content sheet", "code", "Your content sheet", code=SHEET,
              marks=((0, 1), (1, 3), (3, 4)),
              caption="one row per post", said=(
                  ("It starts with a plain sheet. Three columns: the date to post, the "
                   "topic, and a status."),
                  ("You fill in topics when ideas come. Set the status to ready when "
                   "it's good to go."),
                  ("Anything still marked draft is skipped. [confident] You stay in "
                   "charge of what's planned."))),
        Scene("Step 1: today's row", "steps", "Step 1: today's row", items=(
            "Schedule Trigger: every day, at 9",
            "Google Sheets: Get Row(s)",
            "Filter: date is today AND status is ready",
        ), said=(
            ("Add a Schedule Trigger that runs every day, at nine in the morning."),
            ("Then Google Sheets, Get Rows, which reads every row of the sheet. "
             "Connect your Google account once, as a credential."),
            ("Add a Filter with two conditions joined by AND: the date is today, and "
             "the status is ready. Every other row is dropped."))),
        Scene("Step 2: AI drafts", "steps", "Step 2: AI drafts the caption", items=(
            "Basic LLM Chain + your chat model",
            "Prompt: a caption about {{ $json.topic }}",
            "System message: your tone, your rules",
        ), said=(
            ("Next, a Basic LLM Chain. Connect a chat model to it, or it won't do "
             "anything."),
            ("Set the prompt to Define below, and drag in the topic. Something like: "
             "write a short caption about this."),
            ("Then use a system message for your tone. Friendly, short, no prices, "
             "no promises. [emphasis] Your rules go here."))),
        Scene("", "quiz", "A row says status: draft. What happens?", items=(
            "It's posted anyway", "The Filter skips it", "The AI fixes it",
        ), answer=1, caption="Only ready rows for today get past the Filter.", said=(
            ("Quick quiz. Today's row still says draft. What happens to it? Comment "
             "your answer."),
            "B. The Filter skips it. Only ready rows for today go any further.")),
        Scene("Step 3: your approval", "steps", "Step 3: your approval", items=(
            "Slack: Send and Wait for Response",
            "Response Type: Approval",
            "Restrict who can approve: you",
        ), said=(
            ("Here's the important bit. Add a Slack node, with Send and Wait for "
             "Response, and put the caption in the message."),
            ("Set the response type to Approval. The workflow stops right there, and "
             "waits for a click. Approve, or Decline. Your call, every single time."),
            ("And restrict who can approve to you. Leave it empty, and anyone in the "
             "channel could say yes."))),
        Scene("", "subscribe", "Saving you time?", said=(
            "If this takes posting off your to-do list, subscribe.",)),
        Scene("Step 4: post it", "diagram", "Only a yes goes out", items=(
            "If: approved is true",
            "LinkedIn: Post, Create",
            "Sheets: Update Row to posted",
        ), said=(
            ("After the approval, add an If node: approved is true. Only that path "
             "carries on."),
            ("On it, a LinkedIn node: Post, Create, as you or as your company, with "
             "the caption as the text."),
            ("Then update the sheet row to posted. Declined? Nothing is posted, and "
             "you can rewrite it, and set the row back to ready for another day."))),
        Scene("", "pause", "Pause and try this", items=(
            "Add one ready row dated today",
            "Run it by hand, approve in Slack",
        ), said=(
            "Pause the video and add one row to your sheet, dated today, marked ready.",
            "Run the workflow by hand, approve the caption in Slack, and resume.")),
        Scene("When it won't resume", "bullets", "Clicked Approve, nothing?", items=(
            "n8n must be on public HTTPS",
            "localhost won't work for Slack",
            "Add the app's Signing Secret",
        ), said=(
            ("[sighing] You click Approve, and nothing happens. The usual cause is "
             "where n8n runs."),
            ("Slack has to call your n8n back over public HTTPS. On your own laptop, "
             "at localhost, it can't reach you."),
            ("And the Slack credential needs your app's signing secret. Without it, "
             "the buttons show, but clicks don't resume the workflow."))),
        Scene("Before it runs alone", "bullets", "Before it runs alone", items=(
            "Read every caption, out loud",
            "Publish, or the schedule never fires",
            "Watch the first week yourself",
        ), said=(
            ("Before you trust it, read each caption the way a customer would. Does it "
             "sound like you? If not, change the system message."),
            ("Then publish the workflow. A Schedule Trigger only runs a published "
             "workflow, so until then, nothing happens at nine."),
            ("And watch the first week yourself. Approve slowly. Decline anything you "
             "wouldn't say in person."))),
        Scene("", "quiz", "Nobody answers the approval. Is the post sent?", items=(
            "Yes, after a while", "No, it keeps waiting",
        ), answer=1, caption="The If only passes an approved caption.", said=(
            ("Last quiz. Nobody clicks anything. Does the post go out? Comment."),
            ("B, no. The workflow waits, and only an approved caption reaches the "
             "LinkedIn node."))),
        Scene("Recap", "checklist", "Recap", items=(
            "Sheet: date, topic, status",
            "Daily: today's ready rows only",
            "AI drafts with your tone",
            "Slack approval, then LinkedIn",
        ), said=(
            "Recap. A plain sheet holds the whole plan: the date, the topic and the status.",
            "Every morning, n8n picks today's rows that are ready.",
            "A Basic LLM Chain drafts the caption, in your tone.",
            ("[happy] And Slack waits for your approval before anything is posted. "
             "AI does the typing. You keep the final say."))),
    ),
)
