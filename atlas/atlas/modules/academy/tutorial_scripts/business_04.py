"""Business #4: a weekly report sent to Slack from Google Sheets.

Written 2026-10-17 by the script writer (docs/academy-script-writer.md). No
live demo: no new recording can be made, so the picture is steps, bullets,
the message on screen, two quizzes and a pause.

Every product fact was read on the official n8n docs on 2026-10-17:

  docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.scheduletrigger.md
      (Trigger Interval Weeks: "Weeks Between Triggers", "Trigger on
      Weekdays", "Trigger at Hour"; the workflow timezone, else the
      instance's; "You must publish the workflow")
  docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.googlesheets.md
      ("Get Row(s): Read all rows in a sheet")
  docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.filter.md
      (Date & Time "is after or equal to"; "If the item doesn't meet the
      condition, the Filter node omits the item from its output")
  docs.n8n.io/build/work-with-data/handle-special-data-types/work-with-dates-and-times.md
      ("today: a Luxon object containing the current timestamp, rounded
      down to the day"; {{$today.minus({days: 7})}})
  docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.summarize.md
      ("Count: Count the total number of values in your input data"; "Sum:
      Add together the numeric values"; "Fields to Split By ... similar to a
      group by statement")
  docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.slack.md
      (Message: "Send a message"; "you must add the appropriate scopes to
      your Slack app for this node to work")
  docs.n8n.io/integrations/builtin/credentials/slack.md
      (OAuth2 is recommended for the Slack node; a Slack app is needed;
      installing it to the workspace needs a workspace admin; bot scopes
      include chat:write)

The Slack node page doesn't document the Send fields, so the script names
no field of it. No figure is said: the message on screen has placeholders.
"""
from __future__ import annotations

from ..tutorial_series import (
    BUSINESS_PLANNED,
    Episode,
    Scene,
    payoff_scene,
    scripted,
)

MESSAGE = "Monday report: <count> bookings in the last 7 days · by service: <list>"

PICTURE = (
    "Monday, 8 am\nthe schedule runs",
    "Last 7 days\nfiltered from the sheet",
    "One summary\nposted in Slack",
)

EPISODE: Episode = scripted(
    next(e for e in BUSINESS_PLANNED if e.number == 4),
    title_hook="to Send a Weekly Report to Slack with n8n",
    links=(("Schedule Trigger docs",
            "https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.scheduletrigger/"),
           ("Summarize node docs",
            "https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.summarize/"),
           ("Slack node docs",
            "https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.slack/"),
           ("Slack credentials",
            "https://docs.n8n.io/integrations/builtin/credentials/slack/")),
    ask="Which number would you want in your Monday report? Tell me in the comments.",
    topic_said=("Today: a weekly report that reads your sheet every Monday and posts "
                "a short summary in Slack."),
    topic_shown="A weekly report in Slack",
    payoff=payoff_scene(
        ("By the end, your team opens Slack on Monday and last week is already "
         "summed up, without anyone touching a spreadsheet."),
        MESSAGE,
        items=PICTURE,
        caption="the week, in one message"),
    hook=Scene("What we'll build", "hook", "Monday, sorted", items=PICTURE,
               code=MESSAGE, caption="the week, in one message", said=(
                   ("[sighing] Copying numbers out of a sheet every Monday gets old "
                    "fast. Let's make n8n do it."),
                   ("All you need is n8n, a Google Sheet of bookings or orders, and a "
                    "Slack workspace where you can add an app."))),
    needs=("n8n, Cloud or self-hosted", "a Google Sheet with a date column",
           "a Slack workspace where you may add an app"),
    scenes=(
        Scene("Why a weekly report", "bullets", "Why bother?", items=(
            "Everyone sees the same numbers",
            "Nobody has to remember to send it",
            "Same day, same shape, every week",
        ), said=(
            ("Why a weekly report? Because when it lands in Slack, everyone sees the "
             "same numbers, at the same time."),
            ("Nobody has to remember to make it. It just turns up."
             " Even on the week you're on holiday."),
            ("And because it has the same shape every week, you start to notice when "
             "something changes."
             " Slower than last week? You'll see it without opening the sheet."))),
        Scene("Step 1: Monday morning", "steps", "Step 1: Monday morning", items=(
            "Schedule Trigger: Weeks",
            "Monday, at 8",
            "Set the workflow's time zone",
        ), said=(
            "Create a workflow, and add a Schedule Trigger with the Weeks interval.",
            ("Tick Monday as the weekday, and eight as the hour."
             " That's before most teams start, so it's waiting for them."),
            ("Then set the workflow's time zone, so eight in the morning is your eight, "
             "not the server's."))),
        Scene("Step 2: last week's rows", "steps", "Step 2: last week's rows", items=(
            "Google Sheets: Get Row(s)",
            "Filter: date is after or equal to…",
            "…$today.minus({days: 7})",
        ), said=(
            ("Add a Google Sheets node, with Get Rows. It reads every row of your "
             "sheet, one item each."
             " Connect your Google account once, in credentials."),
            ("You only want last week, so add a Filter node. Pick your date column, as "
             "a date, with is after or equal to."),
            ("For the value, use today minus seven days. [emphasis] Rows older than "
             "that are simply dropped."))),
        Scene("", "quiz", "Why the Filter after Get Rows?", items=(
            "To sort the sheet", "To keep only the last 7 days", "To delete old rows",
        ), answer=1, caption="Get Rows reads everything; the Filter keeps last week.", said=(
            ("Quick quiz. Why do we need the Filter after Get Rows? Comment your "
             "answer."),
            ("B. Get Rows reads the whole sheet. The Filter keeps only the last seven "
             "days, and the sheet itself isn't changed."
             " Nothing is deleted."))),
        Scene("Step 3: sum it up", "steps", "Step 3: sum it up", items=(
            "Summarize node",
            "Count the rows, or Sum a number column",
            "Fields to Split By: per service",
        ), said=(
            ("Now add a Summarize node. This turns many rows into a few numbers."
             " Most weeks, one or two numbers are plenty. Don't drown people."),
            ("Choose Count to count the bookings, or Sum to add up a number column, "
             "like hours worked."),
            ("Want it per service? Put the service column in Fields to Split By. It "
             "works like a group by, if you know spreadsheets."))),
        Scene("", "subscribe", "Saving your Mondays?", said=(
            "If this gives you your Monday mornings back, subscribe.",)),
        Scene("Step 4: post to Slack", "steps", "Step 4: post to Slack", items=(
            "Slack node: Message, Send",
            "Connect with OAuth2: a Slack app",
            "Write the message, drag in the numbers",
        ), said=(
            "Last step. Add a Slack node, and choose Message, Send.",
            ("Connecting Slack needs a Slack app. n8n recommends OAuth2, and the app "
             "needs the chat write scope. Installing it may need your workspace "
             "admin."),
            ("Then write a short message, and drag the numbers in from the Summarize "
             "node. [excited] That's your report."
             " Keep it to two or three lines, so people actually read it."))),
        Scene("", "pause", "Pause and try this", items=(
            "Add three test rows dated this week",
            "Run the workflow once by hand",
        ), said=(
            "Pause the video and add three test rows to your sheet, dated this week.",
            "Run the workflow once by hand, and resume when the message shows up.")),
        Scene("Before it runs alone", "bullets", "Before it runs alone", items=(
            "Run it by hand, read the message",
            "Publish, or Monday never comes",
            "Check the first two reports yourself",
        ), said=(
            ("Before you trust it, run it by hand and read the message, out loud if "
             "you like. Does it make sense to someone new?"),
            ("Then publish the workflow. A Schedule Trigger only runs a published "
             "workflow."),
            ("And read the first two Monday reports yourself. If a number looks odd, "
             "check the sheet's dates first."
             " A date typed as plain text is the usual culprit."))),
        Scene("", "quiz", "It worked by hand, but Monday came and nothing. Why?", items=(
            "Slack was down", "The workflow isn't published", "The sheet is too big",
        ), answer=1, caption="A schedule only fires on a published workflow.", said=(
            ("Last quiz. It worked when you ran it by hand, but nothing came on "
             "Monday. Most likely? Comment."),
            "B. The workflow isn't published, so the schedule never fires.")),
        Scene("Recap", "checklist", "Recap", items=(
            "Schedule: Mondays, your time zone",
            "Sheets reads · Filter keeps 7 days",
            "Summarize: count, sum, split by",
            "Slack posts it · publish to go live",
        ), said=(
            "Recap. A Schedule Trigger runs every Monday morning, in your time zone.",
            "Google Sheets reads the rows, and a Filter keeps the last seven days.",
            "Summarize turns them into a count or a sum, split however you like.",
            ("[happy] And Slack posts the report. Publish it, and your Mondays start "
             "with the numbers already there."))),
    ),
)
