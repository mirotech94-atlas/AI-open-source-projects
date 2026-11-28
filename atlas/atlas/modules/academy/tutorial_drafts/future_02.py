# HOLD: needs recording per tutorial_series_ai.HOLDS; script avoids claiming a demo
"""DRAFT, unapproved: needs Miro's approval before it moves to tutorial_scripts/.

Where AI Is Going #2: from chat to agent, why the approval step is the whole
game. An opinion piece.

Written 2026-11-24. No source page was read: this is Atlas's view, labelled as
opinion in every line, with no statistic, date, price or named company plan.
No demo is shown or claimed (the episode is held for a recording; the picture
is bullets, steps and a checklist). Facts are kept to a minimum, and each one
is marked TODO-VERIFY below, with the page to read before approval.

Sources read: none (opinion). Pages to read for the TODO-VERIFY lines:
  code.claude.com/docs/en/permissions
"""
from __future__ import annotations

from ..tutorial_series import FUTURE_PLANNED, Scene, payoff_scene, scripted

STATUS = "draft/unapproved"
OPENER_TOPIC = "an opinion piece on why the approval step is the whole game"

PICTURE = (
    "You ask,\nit answers",
    "It acts,\nyou approve",
    "Trust grows\nstep by step",
)

EPISODE = scripted(
    next(e for e in FUTURE_PLANNED if e.number == 2),
    title_hook="AI Agents",
    title_benefit="Why Approval Matters",
    title_formula=3,
    links=(("Claude Code permissions", "https://code.claude.com/docs/en/permissions"),),
    ask="Which one action would you always want to approve yourself? Tell me below.",
    topic_said=("Today, an opinion: when AI moves from chat to doing things, the "
                "approval step is what makes it usable."),
    topic_shown="Our view: approval is the game",
    payoff=payoff_scene(
        ("By the end, you'll have our view, and one action you'd always approve "
         "yourself."),
        "Our view: the approval step carries the trust",
        items=PICTURE,
        caption="an opinion, not a fact"),
    hook=Scene("What you'll learn", "hook", "An opinion about approval", items=PICTURE,
               caption="an opinion, not a fact", said=(
                   ("[curious] What changes when an AI stops answering and starts "
                    "doing?"),
                   ("I think one small step becomes the whole point: the moment a "
                    "person says yes."),
                   ("All you need is one job you'd hand to an assistant. This is "
                    "opinion, so push back."))),
    needs=("one job you'd hand to an assistant", "five minutes to think"),
    scenes=(
        Scene("Chat versus agent", "bullets", "Chat answers, an agent acts", items=(
            "Chat: you ask, it answers",
            "Agent: it uses tools to do things",
            "Doing has consequences",
        ), said=(
            ("First, plain words. In a chat, you ask and it answers. The worst case "
             "is a bad answer, and you can simply ask again."),
            ("An agent goes further. It uses tools to do things for you, like "
             "sending a message or changing a file."),  # TODO-VERIFY: general description of agents using tools; read the permissions page
            ("[emphasis] And doing has consequences. A bad answer you can ignore. A "
             "sent message you can't."))),
        Scene("Why we call it an opinion", "bullets", "Said plainly: our view", items=(
            "Opinion, not fact",
            "No forecasts",
            "Disagree freely",
        ), said=(
            "Quick label. All of this is our view, not a fact, and not a forecast about where anyone is heading.",
            ("I think it holds up, but I may be wrong. Treat it as one person's "
             "reasoning, not a rule."),
            ("[relaxed] So take what's useful, and argue with the rest."))),
        Scene("The step that matters", "bullets", "The pause before it acts", items=(
            "The agent proposes an action",
            "A person looks at it",
            "Yes, no, or change it",
        ), said=(
            ("Here's the idea. The agent works out what it wants to do, and then it "
             "stops. It proposes."),
            ("A person looks at it. That's the approval step, and it can take two "
             "seconds."),
            ("Yes, no, or change it. That one pause is where I think the trust "
             "comes from, more than from any feature the agent has."))),
        Scene("", "quiz", "What makes an agent trustworthy?", items=(
            "Being smarter", "A pause you control", "Both matter, to me",
        ), answer=-1, caption="No right answer: check your own view.", said=(
            ("Check your own view. What makes you trust an agent more: being "
             "smarter, or a pause you control? Comment."),
            ("No key. I lean toward the pause, because smart can still be wrong. "
             "Where do you land?"))),
        Scene("Why the pause carries trust", "steps", "Why the pause carries trust", items=(
            "You see it before it happens",
            "A mistake costs you a click",
            "You learn how it thinks",
            "You can loosen it later",
        ), said=(
            ("Why does a pause do so much? You see the action before it happens, in "
             "plain words, while you can still say no."),
            "A mistake costs you a click, not a cleanup.",
            ("[confident] Over time you learn how the agent thinks, because you've "
             "read its proposals."),
            ("And later, you can loosen it, for the actions it keeps getting right, "
             "while you keep the pause on the risky ones.")), ),
        Scene("A small example", "bullets", "A small example, in words", items=(
            "Goal: tell a customer we're running late",
            "Agent drafts the message",
            "You read it, then it sends",
        ), said=(
            ("Here's one in words. Your goal is: tell a customer we're running "
             "late. The agent drafts the message."),
            ("It stops there. You read it, fix a word, and say yes. Only then does "
             "it send."),
            ("I think that's the whole trick. The agent did the work, and you "
             "still owned the decision. Fast for you, and still safe to hand over."))),
        Scene("", "subscribe", "Still with me?", said=(
            "If you like opinions you can argue with, subscribe for the next one.",)),
        Scene("Not every action is equal", "bullets", "Not every action is equal", items=(
            "Reading: low worry",
            "Writing: some worry",
            "Sending or deleting: ask first",
        ), said=(
            "Now, I don't think every action needs a stop sign. Reading is low worry.",
            ("Writing something new, a little more. Sending or deleting, a lot more, "
             "because other people or your files are involved."),
            ("[break] So the approval step shouldn't be everywhere. It belongs where "
             "undoing is hard."))),
        Scene("", "pause", "Pause and try this", items=(
            "Pick one job you'd hand over",
            "List its actions, sort by worry",
        ), said=(
            "Pause the video and pick one job you'd hand to an assistant.",
            ("List the actions it would take, and sort them from low worry to high. "
             "Then resume."))),
        Scene("", "quiz", "Which action would you always approve?", items=(
            "Sending a message", "Deleting a file", "Spending anything",
        ), answer=-1, caption="Your pick: no wrong answer.", said=(
            ("Second check. Which one would you always approve yourself? Comment "
             "your pick."),
            ("No key again. Mine's anything that leaves my hands for good. Yours "
             "may differ."))),
        Scene("Where this view could be wrong", "bullets", "Where we could be wrong", items=(
            "Too many pauses get ignored",
            "Our view may age badly",
            "Test it on your own job",
        ), said=(
            ("Where might I be wrong? Too many pauses, and people click yes without "
             "reading. That's a real risk."),
            "And a view like this can age badly. That's what opinions do, so hold it loosely.",
            ("[happy] So try it yourself. Give one job a pause, and see how it "
             "feels."))),
        Scene("Recap", "checklist", "Recap", items=(
            "An opinion, labelled",
            "Chat answers · agents act",
            "The pause carries the trust",
            "Put it where undoing is hard",
        ), said=(
            "Recap. This was an opinion, and I said so.",
            "Our view: a chat answers, and an agent acts.",
            "The approval pause is what makes acting feel safe to hand over, and keeps you in charge.",
            ("[curious] And it belongs where undoing is hard. What's the one action "
             "you'd always approve? Tell us in the comments."))),
    ),
)
