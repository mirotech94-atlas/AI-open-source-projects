"""Agents #6: how to test an AI agent before you trust it.

Study note: docs/academy-references/agents_06.md

Approved by Miro 2026-11-24 (he authorised the review); facts re-read that day.

Written 2026-11-24 as a draft. No live demo: the picture is bullets, steps, a
small test table on screen, two quizzes and a pause.

Official pages read on 2026-11-24 (WebFetch):

  https://www.anthropic.com/engineering/building-effective-agents
      ("We recommend extensive testing in sandboxed environments, along with
      the appropriate guardrails"; "Run many example inputs in our workbench to
      see what mistakes the model makes, and iterate"; "The key to success ...
      is measuring performance and iterating on implementations"; "stopping
      conditions (such as a maximum number of iterations)")
  https://platform.claude.com/docs/en/test-and-evaluate/develop-tests
      (first 100,000 characters only; "Be task-specific: Design evals that
      mirror your real-world task distribution. Don't forget to factor in edge
      cases!"; edge cases: irrelevant or nonexistent input, overly long input,
      poor or harmful input, ambiguous cases; "Automate when possible"
      (multiple-choice, string match, code-graded, LLM-graded); "Prioritize
      volume over quality")
  https://www.anthropic.com/engineering/writing-tools-for-agents
      ("Prompts should be inspired by real-world uses and be based on realistic
      data sources and services")

The shop and its test cases are our own example.

Also read 2026-11-24: platform.claude.com/docs/en/test-and-evaluate/develop-tests
(grading can be code-graded or LLM-graded; the LLM-graded examples use a second
model call as the grader, and "generally best practice" is a different model
from the one that made the output).
"""
from __future__ import annotations

from ..tutorial_series import (
    AGENTS_PLANNED,
    Episode,
    Scene,
    payoff_scene,
    scripted,
)

TESTS = """Request                         Good answer has        Pass?
Where is order 1042?            the real status        yes
Refund my order                 asks a person first    yes
(an empty message)              asks what they need    no"""

PICTURE = (
    "Real requests\nas test cases",
    "Write down\nwhat good is",
    "Run them again\nafter every change",
)

EPISODE: Episode = scripted(
    next(e for e in AGENTS_PLANNED if e.number == 6),
    title_hook="AI Agent Testing",
    title_benefit="Test Sets",
    result="A test set",
    links=(("Building effective agents (Anthropic)",
            "https://www.anthropic.com/engineering/building-effective-agents"),
           ("Create strong empirical evaluations (Claude docs)",
            "https://platform.claude.com/docs/en/test-and-evaluate/develop-tests"),
           ("Writing tools for agents (Anthropic)",
            "https://www.anthropic.com/engineering/writing-tools-for-agents")),
    ask="What is the first request you would put in your agent's test set? Comment.",
    topic_said=("Today: how to test an AI agent before you trust it, with a small set "
                "of test cases you run again and again."),
    topic_shown="A small test set",
    payoff=payoff_scene(
        ("By the end, you'll have a small test set for your agent, and a habit of "
         "running it after every change."),
        "Request → good answer → pass or fail → run again",
        items=PICTURE,
        caption="test it before you trust it"),
    hook=Scene("What you'll learn", "hook", "Test before trust", items=PICTURE,
               code=TESTS, caption="test it before you trust it", said=(
                   ("You tried your agent twice, and it worked. [curious] Does that "
                    "mean it's ready to work alone?"),
                   ("Nothing to install today. Just a pen, and a few real requests "
                    "your agent would get."))),
    needs=("nothing to install", "a pen, and three real requests for your agent"),
    scenes=(
        Scene("Why test", "bullets", "Why test an agent", items=(
            "It picks its own steps",
            "Two good tries prove little",
            "So measure it, and keep improving",
        ), said=(
            ("Why test at all? Because an agent chooses its own steps, so the same "
             "request can go different ways. You can't read its mind, so you check what it does."),
            ("Two good tries prove very little. You've only seen two of the ways "
             "it can go. The third try might be the one that goes wrong."),
            ("Anthropic's guide says the key is measuring performance, and "
             "improving from there. A test set is how you measure."))),
        Scene("Collect real requests", "steps", "Step 1: collect real requests",
              items=(
                  "Open your inbox or chat history",
                  "Copy ten real requests",
                  "Add a few odd ones",
              ), said=(
                  ("Step one: gather real requests. Open your inbox, your chat "
                   "history, your order notes. Anywhere your customers already ask for things."),
                  ("Copy about ten of them. The docs say tests should mirror your "
                   "real tasks, so real ones are best. Ten is enough to start, and you can add more later."),
                  ("Then add the odd ones. An empty message, a very long one, a "
                   "confusing one, a rude one. The docs call these edge cases, and "
                   "agents often slip there."))),
        Scene("", "quiz", "Where do the best test cases come from?", items=(
            "Made-up examples", "Your real requests", "Whatever is shortest",
        ), answer=1, caption="Real tasks, plus a few odd ones.", said=(
            ("Quick quiz. Where do the best test cases come from? Comment your "
             "answer."),
            "B. Your real requests. Made-up ones only test what you already expected, and the surprises are what hurt.")),
        Scene("Write what good is", "code", "A test set on paper", code=TESTS,
              marks=((0, 1), (1, 3), (3, 4)), caption="our own example", said=(
                  ("Step two: write down what a good answer looks like, before you "
                   "run anything. Doing it first keeps you honest. Otherwise you grade the answer you happen to get."),
                  ("Look at the middle column. Order status should be the real "
                   "one. A refund request should end with a person being asked, not with money moving. Keep it short: one line per request is plenty."),
                  ("And the odd request fails. That's fine. A test set that finds "
                   "a gap is doing its job. Now you know what to fix."))),
        Scene("", "subscribe", "Still with me?", said=(
            "If you want agents you can actually trust, subscribe.",)),
        Scene("Grade it", "bullets", "Step 3: grade the answers", items=(
            "Check by rule when you can",
            "Ask another model when you can't",
            "Many small tests beat a few big ones",
        ), said=(
            ("Step three: grading. The docs suggest automating where you can, "
             "with simple checks, like: does the answer contain the order status?"),
            ("Some answers can't be checked by a rule. The docs also list model "
             "graded tests, where a second model reads the answer and says whether it matches what you wrote down."),
            ("And the docs say volume beats polish: more small tests, graded "
             "automatically, beat a few perfect ones. [emphasis] Start small, "
             "though. Ten is plenty."))),
        Scene("", "pause", "Pause and try this", items=(
            "Write three real requests",
            "Write what a good answer is",
        ), said=(
            ("Pause the video and write three real requests your agent would get. "
             "Include one odd one, like an empty message or a request in a different language."),
            ("Next to each, write a short line: what a good answer looks like. "
             "Resume when you have three. Keep that page. It's your first test set."))),
        Scene("Run it again", "steps", "Step 4: run it after every change", items=(
            "Run every test in a safe place",
            "Change one thing at a time",
            "Run all the tests again",
        ), said=(
            ("Step four: run the tests in a sandbox, a safe copy, never against "
             "real customers or real orders. Anthropic recommends testing there, with guardrails."),
            ("Then change one thing: a prompt, a tool, a setting. Just one, so "
             "you know what caused what. Two changes at once, and you can't tell which one broke it."),
            ("Run all the tests again. [relaxed] If something that passed now "
             "fails, you've caught it before a customer did. Then fix it, and run the lot again."))),
        Scene("", "quiz", "You edit the prompt. What do you do next?", items=(
            "Hope it still works", "Run the tests again", "Delete the failures",
        ), answer=1, caption="Every change gets a re-run.", said=(
            ("Last quiz. You just edited your agent's prompt. What's next? "
             "Comment."),
            ("B. Run the tests again. A small change can break an old answer, and you won't notice unless you look."))),
        Scene("Recap", "checklist", "Recap", items=(
            "Real requests, plus odd ones",
            "Write what good looks like first",
            "Grade by rule, or by a second model",
            "Re-run after every change",
        ), said=(
            "Recap. Collect about ten real requests, and a few odd ones, like an empty message.",
            "Write down what a good answer looks like, before you run anything.",
            "Check by rule when you can, and use a second model when you can't.",
            ("[happy] And run it all again after every change. That's how an agent "
             "earns your trust."))),
    ),
)
