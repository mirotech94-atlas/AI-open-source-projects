"""DRAFT, unapproved: needs Miro's approval before it moves to tutorial_scripts/.

AI Explained #2: how a language model actually works (no math).

# HOLD: needs recording per tutorial_series_ai.HOLDS; script avoids claiming a demo

Written 2026-11-24 as a draft. The planned demo (Claude answering one token
at a time, the same prompt three times) cannot be shown, so nothing here says
it was shown. The picture is bullets, steps, a made-up sentence growing on
screen, two quizzes and a pause. The viewer is asked to try the same prompt
three times themselves.

Read read-only on 2026-11-24:

  https://platform.claude.com/docs/en/about-claude/glossary
      (Tokens: "the smallest individual units of a language model", can be
      "words, subwords, characters, or even bytes"; Pretraining: language
      models are "pretrained to predict the next word, given the previous
      context of text in the document"; pretrained models "are not
      inherently good at answering questions or following instructions";
      fine-tuning and RLHF refine them; Temperature: "a parameter that
      controls the randomness of a model's predictions"; higher = "more
      creative and diverse outputs", lower = "more conservative and
      deterministic outputs"; "Even with temperature set to 0, the results
      will not be fully deterministic"; Context window: the text a model
      "can look back on and reference when generating new text", a "working
      memory"; LLM: trained on "vast amounts of text data")
"""
from __future__ import annotations

from ..tutorial_series import Episode, Scene, payoff_scene, scripted
from ..tutorial_series_ai import EXPLAINED_PLANNED

# HOLD: needs recording per tutorial_series_ai.HOLDS; script avoids claiming a demo
STATUS = "draft/unapproved"
# Same wording as tutorial_series_ai.OPENER_TOPICS[("explained", 2)].
OPENER_TOPIC = "how a language model actually writes its answer"

LOOP = (
    "Your question\nread as tokens",
    "The model predicts\nthe next token",
    "That token is added\nto the text",
    "Repeat until\nthe answer is done",
)

GROWING = """The sky is
The sky is blue
The sky is blue today"""

EPISODE: Episode = scripted(
    next(e for e in EXPLAINED_PLANNED if e.number == 2),
    title_hook="a Language Model",
    links=(("Claude glossary: tokens, pretraining, temperature",
            "https://platform.claude.com/docs/en/about-claude/glossary"),),
    ask="Did your three answers match? Tell me what changed in the comments.",
    topic_said=("Today: how a language model writes an answer, one small piece of "
                "text at a time."),
    topic_shown="Token by token",
    payoff=payoff_scene(
        ("By the end, you'll know what a language model does at every step, and "
         "why the same question can come back different."),
        "Same question → same engine → slightly different next token",
        items=LOOP,
        caption="predict, add, repeat"),
    hook=Scene("What you'll learn", "hook", "Predict, add, repeat", items=LOOP,
               caption="predict, add, repeat", said=(
                   ("[curious] Ask an AI the same thing twice and you can get two "
                    "different answers. Is it broken?"),
                   ("No. It's doing exactly what it was built to do, and it's "
                    "simpler than you think."),
                   "You need nothing today. No tools, no code, and no math.")),
    needs=("nothing, no tools and no code", "an AI chat app, for the practice step"),
    scenes=(
        Scene("A next-piece machine", "bullets", "What a language model does", items=(
            "It reads the text so far",
            "It predicts what comes next",
            "Then it does that again",
        ), said=(
            ("Here's the secret, with no math. A language model reads the text so "
             "far."),
            ("Then it predicts what comes next. Just that one thing: the next "
             "piece of text."),
            "Then it does it again, and again, until the answer is finished.")),
        Scene("What a token is", "bullets", "Tokens: the pieces", items=(
            "Small units of text",
            "A word, part of a word, or a single character",
            "The model reads and writes in tokens",
        ), said=(
            ("Those pieces are called tokens. A token is a small unit of text the "
             "model works with."),
            ("It might be a whole word, part of a word, or even one character."),
            ("So when you chat, your words are cut into tokens first, and the "
             "answer comes back as tokens too."))),
        Scene("Watch it grow", "code", "A sentence, one piece at a time", code=GROWING,
              marks=((0, 1), (1, 2), (2, 3)), caption="an invented example",
              said=(
                  ("Here's an invented example, not a real recording. The text so "
                   "far is: the sky is."),
                  ("The model picks a likely next piece, say blue, and adds it. "
                   "Now the text is longer."),
                  ("[emphasis] Then it reads the longer text and predicts again. "
                   "Each new piece depends on everything before it."))),
        # TODO-VERIFY: "the model reads the whole text so far each step" is how the
        # Anthropic glossary's context window entry reads ("can look back on");
        # check docs.anthropic.com context-windows page before approval
        Scene("", "quiz", "At each step, what does the model do?", items=(
            "Looks up the answer", "Predicts the next token", "Searches the web",
        ), answer=1, caption="It predicts the next token, then repeats.", said=(
            ("Quick quiz. At each single step, what does the model do? Comment "
             "your answer."),
            "B, it predicts the next token. Then it goes round again.")),
        Scene("Where it learned this", "bullets", "How it learned to predict", items=(
            "Pretraining: lots of text, guess the next word",
            "Then refined to follow instructions",
        ), said=(
            ("How did it learn? In pretraining, the model reads huge amounts of "
             "text and practises predicting the next word."),
            ("A pretrained model isn't good at following instructions yet. "
             "Extra training turns it into a helpful assistant."))),
        Scene("What it can look back on", "bullets", "Its working memory", items=(
            "The context window: text it can look back on",
            "Your question and the chat so far live there",
            "Different from everything it was trained on",
        ), said=(
            ("While it writes, the model can look back on a certain amount of "
             "text. That's called the context window."),
            ("Your question, and the chat so far, sit in that window. Think of it "
             "as the model's working memory."),
            ("It's separate from the huge pile of text the model was trained on. "
             "Training shaped the model. The window is what it can see right now."))),
        Scene("", "subscribe", "Still with me?", said=(
            "If this is making sense, subscribe for the rest of this series.",)),
        Scene("Why answers differ", "bullets", "Why the answers differ", items=(
            "It chooses among likely next tokens",
            "A setting called temperature controls the spread",
            "Low: safe and steady · High: varied",
        ), said=(
            ("So why do answers differ? Because at each step, there's often more "
             "than one good next piece."),
            ("A setting called temperature controls how much randomness goes into "
             "the choice."),
            ("[confident] Low temperature sticks to the most likely wording. High "
             "temperature allows more variety."))),
        Scene("Not a lookup", "bullets", "Predicting is not looking up", items=(
            "No hidden answer sheet",
            "It predicts what a right answer looks like",
            "Even at zero randomness, repeats can differ",
        ), said=(
            ("This matters. A language model isn't checking a stored answer sheet."),
            ("It predicts what a good answer looks like, one token at a time. "
             "Usually that's right. Sometimes it isn't."),
            ("Even with the randomness setting at its lowest, the same input might "
             "not come back identical every time. Anthropic's own glossary says so."))),
        # TODO-VERIFY: "fluent but not always true"; Anthropic glossary HHH entry says an
        # honest AI avoids hallucinating; read the Anthropic reduce-hallucinations docs page
        Scene("Fluent is not proof", "bullets", "Why it sounds so sure", items=(
            "It's built to write likely, smooth text",
            "Smooth is not the same as true",
        ), said=(
            ("One more thing to carry with you. Because it predicts likely text, "
             "its answers come out smooth and confident every time."),
            ("Smooth isn't the same as true. So for anything that matters, check "
             "it against a source you trust. We'll look at why in a later lesson."))),
        Scene("Try it yourself", "steps", "Same question, three times", items=(
            "Open an AI chat app",
            "Ask one open question, like a name for a cafe",
            "Start a fresh chat and ask it again, twice more",
            "Compare: what stayed, what changed?",
        ), said=(
            "Now try it. Open any AI chat app you use.",
            "Ask one open question. Something like: suggest a name for a small cafe.",
            "Start a fresh chat and ask the exact same thing, two more times.",
            ("Then compare. Some ideas repeat, some change. That's the choosing "
             "between likely tokens, in action."))),
        Scene("", "pause", "Pause and try this", items=(
            "Ask the same question three times",
            "Note what stayed and what changed",
        ), said=(
            "Pause the video and ask your AI the same question three times.",
            "Jot down what stayed the same and what changed, then resume.")),
        Scene("", "quiz", "Why can the same question get different answers?", items=(
            "The model picks among likely tokens", "It forgot the question",
            "It looked up a different page",
        ), answer=0, caption="A bit of randomness in choosing the next token.", said=(
            ("Last quiz. Why can the same question get different answers? "
             "Comment."),
            ("A, it picks among likely next tokens, with a bit of randomness. It "
             "didn't forget, and it isn't looking up pages."))),
        Scene("Recap", "checklist", "Recap", items=(
            "It predicts the next token, then repeats",
            "Tokens are small pieces of text",
            "Answers differ because it chooses among likely tokens",
            "It predicts a right-looking answer, it doesn't look one up",
        ), said=(
            "Recap. A language model predicts the next token, then repeats.",
            "Tokens are the small pieces of text it reads and writes.",
            "Answers differ because it chooses among likely next tokens.",
            ("[happy] And it predicts what a right answer looks like. It doesn't "
             "look one up. Now you know why."))),
    ),
)
