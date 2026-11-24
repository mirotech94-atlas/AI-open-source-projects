"""Business #3: polite, automatic follow-ups for unpaid invoices.

Written 2026-10-17 by the script writer (docs/academy-script-writer.md). No
live demo: no new recording can be made, so the picture is steps, bullets,
the reminder template on screen, two quizzes and a pause.

Every product fact was read on the official n8n docs on 2026-10-17:

  docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.scheduletrigger.md
      (Trigger Interval "Weeks": "Weeks Between Triggers", "Trigger on
      Weekdays", "Trigger at Hour"; the workflow timezone, else the
      instance's; "make sure that you save and publish the workflow")
  docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.googlesheets.md
      and .../googlesheets/sheet-operations.md
      ("Get Row(s): Read all rows in a sheet"; "Update Row: Update a row in
      a sheet"; "Select the Column to Match On when finding the rows to
      update")
  docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.filter.md
      ("If the item doesn't meet the condition, the Filter node omits the
      item from its output"; String "is empty"; Date & Time "is before";
      AND: "When they meet all conditions")
  docs.n8n.io/build/work-with-data/handle-special-data-types/work-with-dates-and-times.md
      ("today: a Luxon object containing the current timestamp, rounded
      down to the day")
  docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.gmail/message-operations.md
      ("Send a message"; To, Subject, Message; "Append n8n attribution: By
      default, the node appends the statement This email was sent
      automatically with n8n ... To remove this statement, turn this option
      off")
  docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.stripe.md
      (read to check: its operations name no invoices and no payment
      links, so this episode names no payment provider; the link is a
      column the viewer fills in from their own)

No price, amount or result is said: the template on screen has no figure.
"""
from __future__ import annotations

from ..tutorial_series import (
    BUSINESS_PLANNED,
    Episode,
    Scene,
    payoff_scene,
    scripted,
)

TEMPLATE = ("Hi Sam, a friendly reminder that invoice INV-7 is still open. "
            "You can pay here: <your link>. Already paid? Thank you, ignore this!")

PICTURE = (
    "Every Monday\nthe schedule runs",
    "Unpaid and overdue\nfiltered from the sheet",
    "One polite email\nand the date logged",
)

EPISODE: Episode = scripted(
    next(e for e in BUSINESS_PLANNED if e.number == 3),
    title_hook="Invoice Reminder Automation",
    links=(("Schedule Trigger docs",
            "https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.scheduletrigger/"),
           ("Google Sheets node docs",
            "https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.googlesheets/"),
           ("Filter node docs",
            "https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.filter/"),
           ("Gmail node docs",
            "https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.gmail/")),
    ask="How do you chase late payments today? Tell me in the comments.",
    topic_said=("Today: polite invoice reminders with your payment link, once a week, "
                "that stop the moment someone pays."),
    topic_shown="Polite invoice follow-ups",
    payoff=payoff_scene(
        ("By the end, every Monday each overdue customer gets one friendly email with "
         "your payment link, and nobody who has paid hears a word."),
        TEMPLATE,
        items=PICTURE,
        caption="one template, only for open invoices"),
    hook=Scene("What we'll build", "hook", "Reminders, without the awkward part",
               items=PICTURE, code=TEMPLATE,
               caption="one template, only for open invoices", said=(
                   ("[sighing] Chasing invoices is nobody's favourite job. So let's hand "
                    "it to n8n, kindly."),
                   ("All you need is n8n, a Google account for Sheets and Gmail, and your "
                    "payment links. Let's build it."))),
    needs=("n8n, Cloud or self-hosted", "a Google account (Sheets and Gmail)",
           "a payment link for each invoice"),
    scenes=(
        Scene("Step 1: the unpaid list", "steps", "Step 1: the unpaid list", items=(
            "One row per invoice",
            "Columns: name, email, invoice, link",
            "Plus: due date, paid on, last reminded",
        ), said=(
            ("Start with a Google Sheet. One row for every invoice you send, and "
             "you're probably keeping a list like this already."),
            ("Give it columns for the customer's name, their email, the invoice "
             "number, and the payment link you already have."),
            ("Then three more: the due date, the date it was paid, and the date of "
             "the last reminder. The last two start empty."))),
        Scene("Step 2: once a week", "steps", "Step 2: once a week", items=(
            "Schedule Trigger: Weeks",
            "Monday, at ten in the morning",
            "Set the workflow's time zone",
        ), said=(
            "Now, in n8n, add a Schedule Trigger, and choose Weeks as the interval.",
            ("Pick Monday as the weekday, and ten as the hour. [emphasis] Once a week "
             "means nobody ever gets more than one reminder a week from you."),
            ("And set the workflow's time zone, so ten means your ten, not the "
             "server's."))),
        Scene("Step 3: only open invoices", "steps", "Step 3: only open invoices", items=(
            "Google Sheets: Get Row(s)",
            "Filter: paid on is empty",
            "And: due date is before today",
        ), said=(
            ("Add a Google Sheets node, with Get Rows. It reads every row of your "
             "sheet, one item per invoice."),
            ("Then add a Filter node. The first condition: the paid on column is "
             "empty. Rows that don't match are simply dropped."),
            ("The second: the due date is before today. Today, in n8n, is the dollar "
             "today expression. Both conditions must be true."))),
        Scene("", "quiz", "A customer paid on Tuesday. On Monday?", items=(
            "They get a reminder anyway",
            "No email: the Filter drops them",
            "The workflow stops",
        ), answer=1, caption="A paid date means no reminder.", said=(
            ("Quick quiz. A customer pays on Tuesday, and you fill in their paid on "
             "date. Do they get Monday's email? Comment your answer."),
            "B. No email. Their row isn't empty any more, so the Filter drops it.")),
        Scene("Step 4: one polite email", "steps", "Step 4: one polite email", items=(
            "Gmail: Message, Send",
            "Name, invoice and link from the row",
            "Options: turn off n8n attribution",
        ), said=(
            ("Add a Gmail node, Message, Send, and connect your Google login once. "
             "The To field is the email column from the row."),
            ("Write one short template. Drag in the name, the invoice number and the "
             "payment link from the row, and say thank you if they've already paid."),
            ("One detail: by default, Gmail in n8n adds a line saying it was sent "
             "with n8n. Turn that option off, and it reads like you."))),
        Scene("", "subscribe", "Getting paid sooner?", said=(
            "If this takes one awkward job off your plate, subscribe.",)),
        Scene("Step 5: log the reminder", "steps", "Step 5: log the reminder", items=(
            "Google Sheets: Update Row",
            "Match on the invoice number",
            "Last reminded: today's date",
        ), said=(
            "Last step. Add one more Google Sheets node, with Update Row.",
            ("Choose the invoice number as the column to match on, so the right row "
             "is updated."),
            ("And write today's date into last reminded. [excited] That's the whole "
             "workflow. Now you can see, at a glance, who was reminded and when, and "
             "answer any customer who asks."))),
        Scene("", "pause", "Pause and try this", items=(
            "Add a test row with your own email",
            "Due yesterday, paid on empty",
        ), said=(
            "Pause the video and add a test row, with your own email address,",
            ("a due date of yesterday, and paid on left empty. Run the workflow once "
             "by hand, and resume when the email arrives."))),
        Scene("Keep it kind", "bullets", "Keep it kind", items=(
            "The sheet is the single source of truth",
            "Fill in paid on the day the money arrives",
            "A reply or a dispute? You answer it",
        ), said=(
            ("A few habits keep this kind. The sheet is the one place that decides "
             "who gets an email, so keep it up to date."),
            ("Fill in the paid on date as soon as a payment arrives, and the "
             "reminders stop by themselves."),
            ("And if someone replies with a question or a problem, a person answers. "
             "The workflow only sends the nudge."))),
        Scene("", "quiz", "Why the Schedule runs only on Mondays", items=(
            "It's cheaper", "At most one reminder a week", "Gmail requires it",
        ), answer=1, caption="Once a week means one reminder a week, at most.", said=(
            ("Last quiz. Why does the schedule run only on Mondays? Comment."),
            "B. So nobody gets more than one reminder a week.")),
        Scene("Recap", "checklist", "Recap", items=(
            "Sheet: one row per invoice, with its link",
            "Schedule: Mondays, your time zone",
            "Filter: not paid, and overdue",
            "Gmail sends it · Sheets logs the date",
        ), said=(
            "Recap. A Google Sheet holds every invoice and its payment link.",
            "A Schedule Trigger runs once a week, on Monday, in your time zone.",
            "A Filter keeps only invoices that are unpaid and overdue.",
            ("[happy] Gmail sends one polite email, and Sheets logs the date. Read the "
             "first few emails yourself before you let it run alone."))),
    ),
)
