"""Business #7: customer feedback replies, drafted by AI and approved by you.

Study note: docs/academy-references/business_07.md

Approved by Miro 2026-11-24 (he authorised the review); facts re-read that day.

Written 2026-11-24 by the script writer as a draft. No live demo: the picture
is steps, bullets, a diagram, two quizzes and a pause.

Official n8n docs read on 2026-11-24 (https://docs.n8n.io/..., .md form):

  integrations/builtin/core-nodes/n8n-nodes-base.formtrigger.md
      (n8n builds the form page; field types include Email and Textarea;
      Form Title and Description; Test URL vs Production URL; "save and
      publish the workflow" before the production URL is used)
  integrations/builtin/cluster-nodes/root-nodes/n8n-nodes-langchain.sentimentanalysis.md
      (a connected language model classifies sentiment; default categories
      Positive, Neutral, Negative; "Text to Analyze"; set the model's
      temperature to 0 or close; connect each output to its own path)
  integrations/builtin/cluster-nodes/root-nodes/n8n-nodes-langchain.chainllm.md
      (Prompt "Define below"; a System message for tone)
  integrations/builtin/app-nodes/n8n-nodes-base.gmail/message-operations.md
      ("Send and Wait for Approval": pauses the workflow until the recipient
      approves; Approve Only, or Approve and Disapprove; Send)

No satisfaction, rating or growth claim is made.

Also read 2026-11-24 (Gmail message operations): Send takes To, Subject, Email
Type and Message; the operations include Reply, Send and Send and Wait for
Approval.
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
    7, 3, "customer-feedback-replies",
    "Customer Feedback Replies with AI in n8n (You Approve Every Reply)",
    "Answer\nfeedback",
    ("A feedback form feeds n8n, AI reads the mood and drafts a reply, and "
     "nothing is sent to the customer until you approve it."),
    ("n8n", "customer feedback automation", "n8n form trigger", "ai sentiment analysis",
     "ai automation for small business"),
    ("#smallbusiness", "#aiautomation", "#n8n"),
    ("A feedback form with an email field",
     "AI reads the mood: positive, neutral, negative",
     "A draft reply in your tone",
     "Your approval by email",
     "Sending, and what to check first",
     "Recap"))

FORM = """Your name        [text]
Your email       [email, required]
How was it?      [textarea, required]"""

PICTURE = (
    "A customer\nleaves feedback",
    "AI reads the mood\nand drafts a reply",
    "You approve\nthen it's sent",
)

EPISODE: Episode = scripted(
    PLANNED,
    title_hook="n8n Feedback Replies",
    title_benefit="You Approve",
    result="Approved replies",
    links=(("Form Trigger",
            "https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.formtrigger/"),
           ("Sentiment Analysis",
            ("https://docs.n8n.io/integrations/builtin/cluster-nodes/root-nodes/"
             "n8n-nodes-langchain.sentimentanalysis/")),
           ("Basic LLM Chain",
            ("https://docs.n8n.io/integrations/builtin/cluster-nodes/root-nodes/"
             "n8n-nodes-langchain.chainllm/")),
           ("Gmail messages",
            ("https://docs.n8n.io/integrations/builtin/app-nodes/"
             "n8n-nodes-base.gmail/message-operations/"))),
    ask="What's the last bit of feedback you got? Tell me in the comments.",
    topic_said=("Today: customers leave feedback, AI drafts a thoughtful reply, and "
                "you approve it before it's sent."),
    topic_shown="Feedback replies, approved by you",
    payoff=payoff_scene(
        ("By the end, every piece of feedback gets a warm reply drafted for you, "
         "and none of them go out until you click approve."),
        "Email to you: 'Reply ready for Sam' · Approve · Disapprove",
        items=PICTURE,
        caption="nothing is sent without your yes"),
    hook=Scene("What we'll build", "hook", "Feedback, answered", items=PICTURE,
               code=FORM, caption="nothing is sent without your yes", said=(
                   ("[sighing] Someone takes the time to tell you what they think, and "
                    "then the reply waits a week."),
                   ("So AI drafts it, and you stay the voice. You need n8n, a Gmail "
                    "account and a chat model."))),
    needs=("n8n, Cloud or self-hosted", "a Gmail account", "a chat model credential"),
    scenes=(
        Scene("The feedback form", "code", "A tiny feedback form", code=FORM,
              marks=((0, 1), (1, 2), (2, 3)),
              caption="three fields are enough", said=(
                  ("It starts with a small form. A name, so the reply can say hello."),
                  ("An email, because that's where the reply goes. Mark it required."),
                  ("And a big text box. [confident] Let people write in their own "
                   "words."))),
        Scene("Step 1: the form", "steps", "Step 1: the form", items=(
            "Form Trigger: n8n builds the page",
            "Add the three fields",
            "Test URL first, Production later",
        ), said=(
            ("Add a Form Trigger node. n8n builds the form page for you, with a title "
             "and a short description."),
            ("Add your three fields: text, email and textarea. Give each one a clear "
             "field name, since you'll drag them in later."),
            ("You get a test link while you build, and a production link for real "
             "customers. Use the test one first."))),
        Scene("Step 2: read the mood", "steps", "Step 2: read the mood", items=(
            "Sentiment Analysis + chat model",
            "Text to Analyze: the feedback box",
            "Positive, Neutral, Negative paths",
        ), said=(
            ("Add a Sentiment Analysis node, and connect a chat model to it. It reads each message and decides how the customer feels."),
            ("Point Text to Analyze at the feedback field. Set the model's temperature "
             "to zero, so the answers stay steady."),
            ("The node sorts each message as positive, neutral or negative. Send each "
             "to its own path. [emphasis] An unhappy customer deserves a different "
             "reply."))),
        Scene("", "quiz", "Why set the model's temperature to zero?", items=(
            "To make it faster", "To keep answers steady", "To make it friendlier",
        ), answer=1, caption="Low temperature means more repeatable answers.", said=(
            ("Quick quiz. Why do we set the temperature to zero here? Comment your "
             "answer."),
            ("B. It keeps the answers as steady as possible, so the same message "
             "doesn't get a different mood each time."))),
        Scene("Step 3: draft the reply", "steps", "Step 3: draft the reply", items=(
            "Basic LLM Chain on each path",
            "System message: your tone, your rules",
            "Different prompt for a negative one",
        ), said=(
            ("On each path, add a Basic LLM Chain. Set the prompt to Define below, "
             "and drag in the name and the feedback."),
            ("The system message holds your tone. Warm, short, and no promises of "
             "refunds or fixes you haven't decided."),
            ("For the negative path, ask for an apology and a question. What can we "
             "do to put it right? You decide the rest."))),
        Scene("", "subscribe", "Enjoying this?", said=(
            "If this helps you answer people faster, subscribe.",)),
        Scene("Step 4: your approval", "diagram", "Only a yes goes out", items=(
            "Gmail: Send and Wait for Approval, to you",
            "Approve and Disapprove buttons",
            "Approved: Gmail Send to the customer",
        ), said=(
            ("Add a Gmail node with Send and Wait for Approval. Send it to yourself, "
             "with the draft and the original feedback in the message."),
            ("Choose Approve and Disapprove. The workflow pauses until you click one."),
            ("After approval, a second Gmail node, Send, mails the reply to the "
             "customer's address. Disapproved? Nothing goes out."))),
        Scene("", "pause", "Pause and try this", items=(
            "Submit the test form twice",
            "One happy, one unhappy message",
        ), said=(
            "Pause the video and open your test link in a new tab. Submit one happy message, with your own email address.",
            "Then submit an unhappy one, check both drafts in your inbox, and resume.")),
        Scene("When the mood is off", "bullets", "Wrong mood?", items=(
            "Try your own real feedback",
            "Add Very Negative, if you need it",
            "Handle sad messages by hand",
        ), said=(
            ("[sighing] Sometimes it reads a joke as a complaint. Test with feedback "
             "you've really had, the awkward ones too."),
            ("The categories can be changed. You could add a very negative one, and "
             "send those to a stricter path."),
            ("And for someone truly upset, a phone call beats any draft. Let n8n alert "
             "you, then answer them yourself."))),
        Scene("Before it runs alone", "bullets", "Before it runs alone", items=(
            "Read each draft as the customer",
            "Edit anything you wouldn't say",
            "Publish, then use the real link",
        ), said=(
            ("Before you trust it, read each draft the way the customer will. Is it "
             "kind, and is it true?"),
            ("Change anything you wouldn't say in person. If it keeps happening, "
             "fix the system message."),
            ("Then publish the workflow. Only then does the production link work "
             "for customers."))),
        Scene("", "quiz", "You never click anything. Is the reply sent?", items=(
            "Yes, later", "No, it waits",
        ), answer=1, caption="Without your approval, nothing is sent.", said=(
            ("Last quiz. The approval email sits unread. Does the customer get a "
             "reply? Comment."),
            ("B, no. The workflow waits for you, and only an approval sends it."))),
        Scene("Recap", "checklist", "Recap", items=(
            "Form Trigger collects feedback",
            "Sentiment Analysis: three paths",
            "AI drafts in your tone",
            "You approve, then it's sent",
        ), said=(
            "Recap. A Form Trigger collects the name, the email and the feedback.",
            "Sentiment Analysis sorts it into positive, neutral or negative.",
            "A chat model drafts the reply in your tone.",
            ("[happy] And Gmail waits for your approval. AI writes the draft. You "
             "send the answer."))),
    ),
)
