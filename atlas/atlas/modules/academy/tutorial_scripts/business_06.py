"""Business #6: AI email triage, with reply drafts you approve.

Study note: docs/academy-references/business_06.md

Approved by Miro 2026-11-24 (he authorised the review); facts re-read that day.

Written 2026-11-24 by the script writer as a draft. No live demo: the picture
is steps, bullets, a diagram, two quizzes and a pause.

Official n8n docs read on 2026-11-24 (https://docs.n8n.io/..., .md form):

  integrations/builtin/trigger-nodes/n8n-nodes-base.gmailtrigger.md
      (event "Message Received"; polling at a Poll Time; Simplify on by
      default; Read Status unread by default; label and search filters)
  integrations/builtin/cluster-nodes/root-nodes/n8n-nodes-langchain.text-classifier.md
      (Categories each have a name and a description; "Allow Multiple
      Classes To Be True"; "When No Clear Match": Discard Item by default,
      or an "Other" branch; one output branch per category)
  integrations/builtin/cluster-nodes/root-nodes/n8n-nodes-langchain.chainllm.md
      (Prompt "Define below"; a System message for tone; chat options only
      apply when a chat model is connected)
  integrations/builtin/app-nodes/n8n-nodes-base.gmail/draft-operations.md
      (Draft: Create, with Subject, Email Type, Message, and the options To
      Email and Thread ID)
  integrations/builtin/app-nodes/n8n-nodes-base.gmail/message-operations.md
      ("Send and Wait for Approval": sends an email and pauses until the
      recipient approves; Approve Only, or Approve and Disapprove; Add
      Label; Send; Reply)

No time-saved, accuracy or cost claim is made.

Also read 2026-11-24 (Text Classifier page): "each item is passed to the model to
determine its category"; categories "have a name and a description"; When No
Clear Match: Discard Item (the default) or "Output on Extra, 'Other' Branch". The
page does not name a connected chat model, so the script does not say how it is
wired.
"""
from __future__ import annotations

from ..tutorial_series import (
    BUSINESS_PLANNED,
    Episode,
    Scene,
    payoff_scene,
    scripted,
)

LABELS = """Question   -> draft a reply for you to approve
Order      -> label it, you handle it
Other      -> left alone in your inbox"""

PICTURE = (
    "A new email\narrives",
    "AI picks a label\nfrom your list",
    "A draft waits\nfor your yes",
)

EPISODE: Episode = scripted(
    next(e for e in BUSINESS_PLANNED if e.number == 6),
    title_hook="n8n AI Email Triage",
    title_benefit="Approve Drafts",
    result="Sorted inbox",
    links=(("Gmail Trigger",
            "https://docs.n8n.io/integrations/builtin/trigger-nodes/n8n-nodes-base.gmailtrigger/"),
           ("Text Classifier",
            ("https://docs.n8n.io/integrations/builtin/cluster-nodes/root-nodes/"
             "n8n-nodes-langchain.text-classifier/")),
           ("Gmail drafts",
            ("https://docs.n8n.io/integrations/builtin/app-nodes/"
             "n8n-nodes-base.gmail/draft-operations/")),
           ("Gmail messages",
            ("https://docs.n8n.io/integrations/builtin/app-nodes/"
             "n8n-nodes-base.gmail/message-operations/"))),
    ask="Which email do you answer most often? Tell me in the comments.",
    topic_said=("Today: AI sorts each new email into a label, and drafts a reply for "
                "the ones that need one."),
    topic_shown="AI email triage",
    payoff=payoff_scene(
        ("By the end, new emails get a label on their own, and the questions come "
         "with a draft reply waiting in your inbox."),
        "Label: Question · Draft ready · nothing sent yet",
        items=PICTURE,
        caption="you read it, you send it"),
    hook=Scene("What we'll build", "hook", "An inbox that sorts itself", items=PICTURE,
               code=LABELS, caption="you read it, you send it", said=(
                   ("[sighing] The same five questions land in your inbox, again and "
                    "again, and each one wants a careful answer."),
                   ("So AI sorts them and writes the first draft. You need n8n, a "
                    "Gmail account and a chat model."))),
    needs=("n8n, Cloud or self-hosted", "a Gmail account", "a chat model credential"),
    scenes=(
        Scene("The three labels", "code", "Three simple labels", code=LABELS,
              marks=((0, 1), (1, 2), (2, 3)),
              caption="few labels, plain names", said=(
                  ("Start small. Three labels are plenty, and you can add more later. Question means someone needs "
                   "an answer from you."),
                  ("Order means it's about something they bought. You'll handle those "
                   "yourself."),
                  ("And Other is everything else. [confident] Fewer labels means fewer "
                   "mistakes."))),
        Scene("Step 1: new email in", "steps", "Step 1: new email in", items=(
            "Gmail Trigger: Message Received",
            "Pick a poll time",
            "Read status: unread",
        ), said=(
            ("Add a Gmail Trigger, with the event Message Received. Connect your "
             "Google account once."),
            ("It checks for new mail at the poll time you choose. It isn't instant, "
             "and for an inbox that's fine. Nobody expects a reply in one second."),
            ("Leave read status on unread. Only new emails start the workflow, so your old inbox is left alone, and nothing runs twice."))),
        Scene("Step 2: AI sorts it", "steps", "Step 2: AI sorts it", items=(
            "Text Classifier + your chat model",
            "Categories: a name and a description",
            "Other branch for no clear match",
        ), said=(
            ("Next, a Text Classifier node. Point its input at the email text. Each "
             "email goes to the model for sorting."),
            ("Add your categories. Each one has a name and a description, and the "
             "description is how the model learns what you mean."),
            ("Turn on the Other branch. By default, an email that matches nothing is "
             "simply dropped. [emphasis] You don't want to lose mail."))),
        Scene("", "quiz", "An email fits no category. By default?", items=(
            "It's sent anyway", "It's discarded", "It's labelled Order",
        ), answer=1, caption="Turn on the Other branch to keep it.", said=(
            ("Quick quiz. An email matches none of your categories. What happens by "
             "default? Think about your customers, then comment your answer."),
            ("B. It's discarded. So switch on the Other branch, and nothing is "
             "lost."))),
        Scene("Step 3: a draft reply", "steps", "Step 3: a draft reply", items=(
            "Basic LLM Chain on the Question branch",
            "System message: your tone, your rules",
            "Gmail: Draft, Create",
        ), said=(
            ("On the Question branch, add a Basic LLM Chain. Set the prompt to Define "
             "below, and drag in the email."),
            ("Use the system message for your tone. Friendly, short, and never "
             "promise anything you haven't checked."),
            ("Then a Gmail node, Draft, Create. Put the reply in the message, the "
             "sender in To Email, and the Thread ID. It lands as a draft in the same thread, not sent."))),
        Scene("", "subscribe", "Saving you time?", said=(
            "If this clears your inbox, subscribe.",)),
        Scene("Step 4: you approve", "diagram", "Nothing leaves without you", items=(
            "Label the email in Gmail",
            "Draft waits in your Drafts folder",
            "Or: Send and Wait for Approval",
        ), said=(
            ("Add a Gmail node, Add Label, so each email shows its label. Now your "
             "inbox is sorted."),
            ("The draft sits in Drafts. You read it, change a word, and press send "
             "yourself."),
            ("Want a button instead? Gmail has Send and Wait for Approval. It pauses "
             "the workflow until you click."))),
        Scene("", "pause", "Pause and try this", items=(
            "Send yourself a question email",
            "Run the workflow, check Drafts",
        ), said=(
            "Pause the video and email yourself a real question, from another address.",
            "Run the workflow by hand, then look in Drafts. Is the label right, and does the reply sound like you? Resume when you've checked.")),
        Scene("Before it runs alone", "bullets", "Before it runs alone", items=(
            "Read every draft like a customer",
            "Never auto-send a reply",
            "Check your Other pile weekly",
        ), said=(
            ("Before you trust it, read each draft the way the customer will. Is it "
             "kind, and is it correct? If not, fix the system message."),
            ("Keep a human in the loop for every reply. Drafts and approval buttons are "
             "there for a reason."),
            ("And glance at your Other pile now and then. If real questions end up "
             "there, add a category, or sharpen one."))),
        Scene("When it gets it wrong", "bullets", "Wrong label?", items=(
            "Sharpen the category description",
            "Test with your own emails",
            "Publish, then watch a week",
        ), said=(
            ("[sighing] Sometimes it picks the wrong label. Don't panic. Rewrite that "
             "category's description, in plain words."),
            ("Test with real emails of yours, the odd ones too. Customers write in "
             "strange ways."),
            ("Then publish the workflow, and read every draft for a week. Only then "
             "trust it a little more."))),
        Scene("", "quiz", "The AI wrote a draft. Has it been sent?", items=(
            "Yes, straight away", "No, it waits as a draft",
        ), answer=1, caption="Create makes a draft. You send it.", said=(
            ("Last quiz. The draft is written. Has the customer got it? Comment."),
            ("B, no. A draft is only a draft. Nothing is sent until you do it."))),
        Scene("Recap", "checklist", "Recap", items=(
            "Gmail Trigger: new unread mail",
            "Text Classifier: a few labels",
            "Draft reply in your tone",
            "You read it, you send it",
        ), said=(
            "Recap. A Gmail Trigger starts the workflow for each new unread email.",
            "A Text Classifier gives it one of a few labels, with an Other branch.",
            "A chat model writes the draft, in your tone, and Gmail saves it.",
            ("[happy] You stay the editor. AI sorts and drafts, and you press send."))),
    ),
)
