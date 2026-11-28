"""Business #8: a monthly summary of your sheet, written in plain words by AI.

Study note: docs/academy-references/business_08.md

Approved by Miro 2026-11-24 (he authorised the review); facts re-read that day.

Written 2026-11-24 by the script writer as a draft. No live demo: the picture
is steps, bullets, the message on screen, two quizzes and a pause.

Official n8n docs read on 2026-11-24 (https://docs.n8n.io/..., .md form):

  integrations/builtin/core-nodes/n8n-nodes-base.scheduletrigger.md
      (Months interval: "Months Between Triggers", "Trigger at Day of Month"
      from 1 to 31, "If a month doesn't have this day, the node won't
      trigger"; hour and minute; workflow time zone; "You must publish the
      workflow")
  integrations/builtin/core-nodes/n8n-nodes-base.summarize.md
      (Count; Sum; Fields to Split By works like a group-by; Output Format:
      each split in its own item, or all splits in a single item)
  integrations/builtin/core-nodes/n8n-nodes-base.aggregate.md
      (groups separate items into one item with a list; "All Item Data")
  integrations/builtin/cluster-nodes/root-nodes/n8n-nodes-langchain.chainllm.md
      (Prompt "Define below", static text or an expression; a System message
      for tone)

Sibling modules (business_04, business_05) cite on 2026-10-17 and 2026-10-18:
Google Sheets Get Row(s), Filter on dates, the Slack node Message > Send.

No figure is said: the message on screen has placeholders.

Also read 2026-11-24: Google Sheets "Get Row(s)" (sheet operations page); Filter
node conditions include Date & Time "is after" and "is before"; Slack Message
operations include "Send a message" (n8n-nodes-base.slack).
"""
from __future__ import annotations

from ..tutorial_series import (
    Episode,
    Scene,
    payoff_scene,
    plan,
    scripted,
)

PLANNED = plan(
    8, 3, "monthly-summary-ai",
    "Monthly Business Summary with AI in n8n (Sheet to Slack, Plain Words)",
    "Monthly\nsummary",
    ("Once a month, n8n counts your sheet, AI writes the numbers up in plain "
     "words, and the note lands in Slack."),
    ("n8n", "monthly report automation", "n8n schedule trigger months",
     "n8n basic llm chain", "ai automation for small business"),
    ("#smallbusiness", "#aiautomation", "#n8n"),
    ("The sheet and the numbers you want",
     "A Schedule Trigger for the first of the month",
     "Summarize turns rows into a few numbers",
     "AI writes them up, using only those numbers",
     "Posting it to a private Slack channel",
     "Recap"))

MESSAGE = ("Last month: <count> new orders, mostly <top service>. "
           "Numbers from your sheet, read just now.")

PICTURE = (
    "The 1st of the month\nthe schedule runs",
    "Summarize counts\nthe sheet's rows",
    "AI writes it up\nSlack gets the note",
)

EPISODE: Episode = scripted(
    PLANNED,
    title_hook="n8n Monthly Summary",
    title_benefit="Written by AI",
    result="Monthly summary",
    links=(("Schedule Trigger",
            ("https://docs.n8n.io/integrations/builtin/core-nodes/"
             "n8n-nodes-base.scheduletrigger/")),
           ("Summarize node",
            "https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.summarize/"),
           ("Aggregate node",
            "https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.aggregate/"),
           ("Basic LLM Chain",
            ("https://docs.n8n.io/integrations/builtin/cluster-nodes/root-nodes/"
             "n8n-nodes-langchain.chainllm/"))),
    ask="Which number do you check every month? Tell me in the comments.",
    topic_said=("Today: a monthly summary. n8n counts your sheet, and AI writes the "
                "numbers up in plain words."),
    topic_shown="A monthly summary, in plain words",
    payoff=payoff_scene(
        ("By the end, a short note about last month shows up in Slack on the first, "
         "written for you, from your own numbers."),
        MESSAGE,
        items=PICTURE,
        caption="your numbers, in plain words"),
    hook=Scene("What we'll build", "hook", "Last month, in a note", items=PICTURE,
               code=MESSAGE, caption="your numbers, in plain words", said=(
                   ("[sighing] Month end comes, and you're staring at a sheet, trying "
                    "to remember what it all meant."),
                   ("So n8n counts, and AI writes it up. You need n8n, a Google Sheet, "
                    "a Slack workspace and a chat model."))),
    needs=("n8n, Cloud or self-hosted", "a Google Sheet with a date column",
           "a Slack workspace where you may add an app", "a chat model credential"),
    scenes=(
        Scene("Why a monthly note", "bullets", "Why a monthly note?", items=(
            "A month shows the shape",
            "Numbers first, words second",
            "Same note, same day",
        ), said=(
            ("A weekly report shows you the bumps. A monthly note shows you the "
             "shape, and that's easier to read."),
            ("The numbers come from n8n, not from the AI. The AI only turns them into "
             "a few plain sentences."),
            ("And it arrives on the same day, in the same shape, so you can compare "
             "it with last month's."))),
        Scene("Step 1: the first of the month", "steps", "Step 1: the first of the month",
              items=(
                  "Schedule Trigger: Months",
                  "Trigger at Day of Month: 1",
                  "Set the time zone, and publish",
              ), said=(
                  ("Add a Schedule Trigger, and pick the Months interval."),
                  ("Set the day of the month to one. Every month has a first. A "
                   "thirty-first, though, would skip the short months."),
                  ("Set the workflow's time zone, and publish it. A schedule only "
                   "runs a published workflow."))),
        Scene("Step 2: count the rows", "steps", "Step 2: count the rows", items=(
            "Google Sheets: Get Row(s)",
            "Filter: date is last month",
            "Summarize: Count, split by service",
        ), said=(
            ("Add Google Sheets, Get Rows. Then a Filter on your date column, so only "
             "last month's rows go on."),
            ("Then a Summarize node. Count the rows, and put the service column in "
             "Fields to Split By, like a group by."),
            ("Choose all splits in a single item. [emphasis] Then the AI gets one tidy "
             "list, instead of many little ones."))),
        Scene("", "quiz", "Where do the numbers come from?", items=(
            "From the AI's memory", "From the Summarize node",
        ), answer=1, caption="Summarize counts. The AI only words it.", said=(
            ("Quick quiz. Who works out the numbers in the note? Comment your answer."),
            ("B. The Summarize node counts them, from your sheet. The AI never "
             "guesses a number."))),
        Scene("Step 3: AI writes it up", "steps", "Step 3: AI writes it up", items=(
            "Basic LLM Chain + your chat model",
            "Prompt: the counts, as an expression",
            "System message: use only these numbers",
        ), said=(
            ("Add a Basic LLM Chain, and connect a chat model. Set the prompt to "
             "Define below, and drag in the counts."),
            ("Ask for three short sentences, in plain words. What went up or down, "
             "and which service led."),
            ("In the system message, say: use only the numbers given. Never add "
             "one. [confident] Your rule beats its guess."))),
        Scene("", "subscribe", "Saving you time?", said=(
            "If this saves your month end, subscribe.",)),
        Scene("Step 4: post it", "steps", "Step 4: post it", items=(
            "Slack: Message, Send",
            "A private channel, just for you",
            "Add the sheet link to the note",
        ), said=(
            ("Add a Slack node, Message, Send, and drag in the AI's text. Connect it the same way you did for the weekly report."),
            ("Send it to a private channel, with only you in it. Read it first, then "
             "forward it to the team."),
            ("Add a link to the sheet, so anyone can check the numbers behind the "
             "words."))),
        Scene("", "pause", "Pause and try this", items=(
            "Add test rows dated last month",
            "Run the workflow by hand",
        ), said=(
            "Pause the video and add a few test rows to your sheet, dated last month.",
            "Run the workflow by hand, compare the note with your sheet, and resume.")),
        Scene("When it's quiet", "bullets", "Nothing arrived?", items=(
            "Is the workflow published?",
            "Is the day one, and the hour set?",
            "Is the Filter's date a real date?",
        ), said=(
            ("[sighing] The first of the month came, and no note. Check the simple "
             "thing first. Is the workflow published?"),
            ("Then the schedule. The day should be one, with an hour and a time zone "
             "you picked on purpose."),
            ("And the sheet. A date typed as plain text won't filter as a date, so "
             "your Filter keeps nothing."))),
        Scene("Before it runs alone", "bullets", "Before it runs alone", items=(
            "Check every number against the sheet",
            "Re-read the wording out loud",
            "Publish, then read the first two",
        ), said=(
            ("Before you trust it, check each number in the note against the sheet. "
             "If one is wrong, fix the Filter first."),
            ("Read the wording out loud. Does it sound like you? If not, adjust the "
             "system message, and run it again."),
            ("Then publish the workflow, and read the first two notes yourself, "
             "before you share them."))),
        Scene("", "quiz", "A month has no sales rows. What should the note say?", items=(
            "Invent a number", "That there were none",
        ), answer=1, caption="Never guess a number: say what the sheet says.", said=(
            ("Last quiz. The sheet has no rows for the month. What should the note "
             "say? Comment."),
            ("B. That there were none. The AI should never invent a number, so "
             "your system message says so."))),
        Scene("Recap", "checklist", "Recap", items=(
            "Schedule: Months, day one",
            "Sheets and Filter: last month",
            "Summarize counts, AI words it",
            "Slack, private, you check first",
        ), said=(
            "Recap. A Schedule Trigger runs on the first of every month.",
            "Google Sheets and a Filter pick out last month's rows.",
            "Summarize counts them, and a chat model writes them up in plain words.",
            ("[happy] Slack gets the note, and you check it first. The numbers are "
             "yours, and the words are drafted."))),
    ),
)
