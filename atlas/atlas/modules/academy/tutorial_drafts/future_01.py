# HOLD: needs recording per tutorial_series_ai.HOLDS; script avoids claiming a demo
"""DRAFT, unapproved: needs Miro's approval before it moves to tutorial_scripts/.

Where AI Is Going #1: the model isn't the bottleneck any more. An opinion piece.

Written 2026-11-24. No source page was read: this is Atlas's view, labelled as
opinion in every line, with no statistic, date, price or named company plan.
No demo is shown or claimed (the episode is held for a recording; the picture
is bullets, steps and a checklist). Facts are kept to a minimum, and each one
is marked TODO-VERIFY below, with the page to read before approval.

Sources read: none (opinion). Pages to read for the TODO-VERIFY lines:
  docs.claude.com/en/docs/build-with-claude/prompt-engineering/overview
"""
from __future__ import annotations

from ..tutorial_series import FUTURE_PLANNED, Scene, payoff_scene, scripted

STATUS = "draft/unapproved"
OPENER_TOPIC = "an opinion piece on why the model isn't the bottleneck any more"

PICTURE = (
    "Same task,\ndifferent models",
    "Better input\nin front of it",
    "Better result,\nevery time",
)

EPISODE = scripted(
    next(e for e in FUTURE_PLANNED if e.number == 1),
    title_hook="AI Models",
    title_benefit="Why Input Wins",
    title_formula=3,
    links=(("Prompt engineering overview",
            "https://docs.claude.com/en/docs/build-with-claude/prompt-engineering/overview"),),
    ask="What's the one thing you now always tell the AI first? Tell me below.",
    topic_said=("Today, an opinion: why what you give the AI matters more than "
                "which AI you pick."),
    topic_shown="Our view: input beats model",
    payoff=payoff_scene(
        ("By the end, you'll have our view, and a short checklist for your own next "
         "request."),
        "Our view: a good input beats a fancier model",
        items=PICTURE,
        caption="an opinion, not a fact"),
    hook=Scene("What you'll learn", "hook", "An opinion about input", items=PICTURE,
               caption="an opinion, not a fact", said=(
                   ("[curious] Ever switched to a newer AI, and got the same "
                    "so-so answer?"),
                   ("I think that's a clue. Our view is that the input is doing "
                    "more of the work than the model."),
                   ("All you need is a task you do often, and five minutes. This "
                    "one's opinion, so argue back."))),
    needs=("a task you do often", "five minutes to think"),
    scenes=(
        Scene("Why this is an opinion", "bullets", "Said plainly: our view", items=(
            "Opinion, not fact",
            "No numbers, no forecasts",
            "You decide what you think",
        ), said=(
            ("First, a promise. Everything here is our view. I think it's useful, "
             "but I'm not claiming it's proven."),
            ("No statistics and no predictions. Just how it looks from where we sit, "
             "after watching a lot of people use these tools."),
            ("[relaxed] And you're allowed to disagree. Actually, I'd like that."))),
        Scene("The model gets the blame", "bullets", "The model gets the blame", items=(
            "A weak answer? Blame the model",
            "Switch, and hope",
            "Rarely the whole story",
        ), said=(
            ("Here's what I think happens. An answer comes back weak, and we blame "
             "the model."),
            ("So we switch to another one, and hope. Then the next answer comes back "
             "just as flat, and we feel stuck."),
            ("[emphasis] Our view: that's rarely the whole story. Often, the AI "
             "just wasn't given enough."))),  # TODO-VERIFY: that prompt quality drives results, per the prompt engineering overview page
        Scene("A thought experiment", "bullets", "Same task, same words", items=(
            "Picture one vague request",
            "Hand it to any decent model",
            "You'd get a vague answer",
        ), said=(
            ("Try this in your head. Picture one vague request, like help me with my "
             "email, with nothing else around it."),
            ("Hand it to any decent model. I think you'd get a polite, general, "
             "forgettable answer."),
            ("Not because the model is weak. Because nobody told it who the email "
             "is for, or what you want the reader to do next."))),
        Scene("", "quiz", "Whose fault is a vague answer?", items=(
            "The model", "The input", "Both, in my view",
        ), answer=-1, caption="No right answer: check your own view.", said=(
            ("Check your own view. When an answer comes back vague, whose fault "
             "is it, usually? Comment."),
            ("There's no key here. Mine's the input, most of the time. Yours "
             "might differ, and that's fine."))),
        Scene("What a good input has", "steps", "What a good input has in it", items=(
            "Who it's for",
            "What done looks like",
            "An example you like",
            "What to leave out",
        ), said=(
            "So what's in a good input? Who the answer is for, to start.",
            ("Then what done looks like, so the AI knows when to stop. Three short "
             "lines, say, or one clear question at the end."),
            ("[confident] Add an example you like. One example says more than a "
             "paragraph of adjectives."),
            ("And what to leave out. Boundaries help as much as instructions, and they "
             "take ten seconds to write.")), ),
        Scene("A small example", "bullets", "A small example, in words", items=(
            "Vague: help me with my email",
            "Better: a reply to a customer, three lines, warm",
            "Same model, different result",
        ), said=(
            ("Here's one in words. The vague version is just: help me with my "
             "email. That's all the AI gets."),
            ("The better version says: reply to this customer, three lines, warm "
             "and plain, no promises about dates."),
            ("I think you'd feel the difference at once, and the model never "
             "changed. Only what you gave it did."))),
        Scene("", "subscribe", "Still with me?", said=(
            "If you like opinions you can argue with, subscribe for the next one.",)),
        Scene("Why it matters more now", "bullets", "Why we think this is growing", items=(
            "Models are getting easier to use",
            "So the gap moves to you",
            "Your context is yours alone",
        ), said=(
            ("Why does this matter more now? I think models are getting easier to "
             "use, honestly."),  # TODO-VERIFY: "models are getting easier to use" is a general claim; soften or cite before approval
            ("If so, the gap moves to what you bring. And nobody can bring that "
             "for you."),
            ("[break] Your goals, your customers, your style. That's yours alone, "
             "and it's the part the model can't guess, however clever it is."))),
        Scene("", "pause", "Pause and try this", items=(
            "Pick one task you do often",
            "Write who, what done looks like, one example",
        ), said=(
            "Pause the video and pick one task you do often.",
            ("Write down who it's for, what done looks like, and one example you "
             "like. Then resume."))),
        Scene("", "quiz", "What would you add first?", items=(
            "Who it's for", "An example", "What to leave out",
        ), answer=-1, caption="Your pick: no wrong answer.", said=(
            ("Second check. Of those three, which one would you add first? "
             "Comment."),
            ("Again, no key. I'd start with an example. But you know your task "
             "better than I do."))),
        Scene("Where this view could be wrong", "bullets", "Where we could be wrong", items=(
            "Some tasks do need a stronger model",
            "Our view may age badly",
            "Test it on your own work",
        ), said=(
            ("Now, where I might be wrong. Some tasks may well need a stronger "
             "model. I'm not saying never."),
            "And a view like this can age badly. That's the risk of opinions.",
            ("[happy] So test it on your own work. Same task, better input, and "
             "see what you think. If nothing changes for you, tell me. I want to hear it."))),
        Scene("Recap", "checklist", "Recap", items=(
            "An opinion, labelled",
            "Weak answers often mean thin input",
            "Who, done, example, leave out",
            "Test it on your own task",
        ), said=(
            "Recap. This was an opinion, and I said so, because that matters.",
            "Our view: a weak answer often means the input was thin, not that the model was weak.",
            "A good input says who it's for, what done is, an example, and what to skip.",
            ("[curious] Now test it on your own task. What's the first thing you'll "
             "add? Tell us in the comments."))),
    ),
)
