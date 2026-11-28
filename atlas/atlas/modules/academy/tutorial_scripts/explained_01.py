"""Approved by Miro 2026-11-24 (he authorised the review); facts re-read that day.

Study note: docs/academy-references/explained_01.md

AI Explained #1: AI vs machine learning vs deep learning vs generative AI.

Written 2026-11-24 as a draft. No live demo: the picture is a nested-box
diagram, bullets, steps, two quizzes and a pause.

Definitions were read read-only on 2026-11-24:

  https://www.ibm.com/think/topics/ai-vs-machine-learning-vs-deep-learning-vs-neural-networks
      (the terms are "a series of AI systems from largest to smallest, each
      encompassing the next"; AI = "machines that mimic human intelligence
      and human cognitive functions like problem-solving and learning";
      machine learning = "a subset of AI"; deep learning = "a subfield of
      machine learning", a neural network with more than three layers
      counts as deep learning; neural networks = "the backbone of deep
      learning"; examples: recommendations from past purchases, speech and
      image recognition)
  https://www.ibm.com/think/topics/generative-ai
      (generative AI = AI "that can create original content such as text,
      images, video, audio or software code"; it depends on "deep learning
      models" that find patterns in large datasets and produce new content)
  https://platform.claude.com/docs/en/about-claude/glossary
      (a large language model is trained on "vast amounts of text data" and
      can generate text, answer questions and summarize)

  https://www.ibm.com/think/topics/machine-learning
      (an email spam filter is the page's own example of machine learning; rules-
      based AI is explicit if-then logic programmed by people, unlike machine
      learning, where "the logic ... isn't explicitly programmed")
  The IBM AI-vs-ML page also says deep learning can take raw "text, images" and
  names speech and image recognition.
"""
from __future__ import annotations

from ..tutorial_series import Episode, Scene, payoff_scene, scripted
from ..tutorial_series_ai import EXPLAINED_PLANNED

BOXES = (
    "AI\nthe big box",
    "Machine learning\nlearns from examples",
    "Deep learning\nmany layers",
    "Generative AI\nmakes new things",
)

EPISODE: Episode = scripted(
    next(e for e in EXPLAINED_PLANNED if e.number == 1),
    result="Four clear terms",
    title_hook="AI vs Machine Learning",
    links=(("IBM: AI vs machine learning vs deep learning",
            "https://www.ibm.com/think/topics/ai-vs-machine-learning-vs-deep-learning-vs-neural-networks"),
           ("IBM: what is generative AI", "https://www.ibm.com/think/topics/generative-ai"),
           ("Claude glossary", "https://platform.claude.com/docs/en/about-claude/glossary")),
    ask="Which tool do you use every day, and which box is it in? Tell me in the comments.",
    topic_said=("Today: four words everyone mixes up, AI, machine learning, deep "
                "learning and generative AI."),
    topic_shown="Four boxes, one picture",
    payoff=payoff_scene(
        ("By the end, you'll sort any AI tool you meet into the right box, and "
         "know why it fits there."),
        "Spam filter → machine learning · Claude writing a reply → generative AI",
        items=BOXES,
        caption="four words, four boxes"),
    hook=Scene("What you'll learn", "hook", "Four words, one picture", items=BOXES,
               caption="four words, four boxes", said=(
                   ("[sighing] AI, machine learning, deep learning. People use them "
                    "like they're all the same thing. They aren't."),
                   ("They're boxes, one inside the next. Once you see the picture, "
                    "the confusion goes away."),
                   "You need nothing today. No tools, no code. Just a pen if you like.")),
    needs=("nothing, no tools and no code", "a pen, if you like to sketch"),
    scenes=(
        Scene("Four nested boxes", "diagram", "Each box sits inside the last", items=BOXES,
              said=(
                  ("Picture a big box. That's AI, the widest idea of the four."),
                  ("Inside it sits a smaller box, machine learning. Software that "
                   "learns from examples."),
                  ("Inside that, deep learning. And generative AI is the part that "
                   "makes new things."),
                  ("[emphasis] One idea inside the next. That's the whole map."))),
        Scene("AI: the big box", "bullets", "AI: the big box", items=(
            "Machines that act in ways we call smart",
            "Problem-solving, learning, translating",
            "The umbrella over everything else",
        ), said=(
            ("AI means machines that mimic human thinking. Things like solving a "
             "problem, or learning."),
            ("Translation counts. So does recognising a voice or a face."),
            "It's the umbrella. Every other word today lives underneath it.")),
        Scene("Machine learning", "bullets", "Machine learning: rules from examples", items=(
            "You don't write the rules",
            "It finds them in examples",
            "More examples, fewer guesses",
        ), said=(
            ("Machine learning is a smaller box inside AI. You don't type out the "
             "rules yourself."),
            ("Instead you show it lots of examples, and it finds the pattern. "
             "Think of a shop suggesting what you might like, based on what you "
             "bought before."),
            "The more good examples it sees, the fewer wrong guesses it makes.")),
        Scene("", "quiz", "A filter learns from examples to catch spam. Which box?", items=(
            "Machine learning", "Generative AI", "Neither",
        ), answer=0, caption="It learns the pattern from examples: machine learning.",
              said=(
            ("Quick quiz. A spam filter learns from examples of spam. Which box "
             "does that describe? Comment your answer."),
            "A, machine learning. It learned the pattern from examples.")),
        Scene("Deep learning", "bullets", "Deep learning: many layers", items=(
            "A kind of machine learning",
            "Uses neural networks with many layers",
            "Great at pictures, voices and text",
        ), said=(
            ("Deep learning is machine learning again, one box further in. Same "
             "idea, learning from examples."),
            ("The difference is the tool. It uses a neural network, which is "
             "layers of tiny connected steps. With many layers, it's called deep."),
            ("[confident] That depth helps with messy things like photos, speech "
             "and writing."))),
        Scene("", "subscribe", "Still with me?", said=(
            "If this is making the jargon click, subscribe for the next lesson.",)),
        Scene("Generative AI", "bullets", "Generative AI: makes new things", items=(
            "Creates new text, images, audio or code",
            "Built on deep learning models",
            "You ask, it makes",
        ), said=(
            ("Now the newest box. Generative AI makes new content: text, pictures, "
             "audio, even code."),
            ("Under the hood it's deep learning, trained on huge piles of "
             "examples."),
            ("[excited] You write a request, it makes something new. Claude "
             "writing you a reply is exactly that."))),
        Scene("Common mix-ups", "bullets", "Three mix-ups to avoid", items=(
            "Not all AI is generative AI",
            "Not all AI learns from examples",
            "Generative AI is not a separate world",
        ), said=(
            ("First mix-up. Generative AI is only one corner. A tool can be AI "
             "without making anything new, like a spam filter that only sorts mail."),
            ("Second, careful: this is a simple map. Some AI is written as plain "
             "rules by hand, with no learning at all. It still counts as AI."),
            ("Third, generative AI isn't a rival to deep learning. It's built on "
             "it, so it lives inside that box, not beside it."))),
        Scene("Why the boxes help", "bullets", "Why bother with the boxes?", items=(
            "You read headlines with less confusion",
            "You ask better questions of a product",
        ), said=(
            ("So why bother? Because the words get thrown around in headlines and "
             "ads, often as if they all mean the same thing."),
            ("With the boxes in your head, you can ask a plain question: what "
             "does this tool actually do, and how did it learn to do it?"))),
        Scene("Sort your tools", "steps", "Sort three tools into boxes", items=(
            "Pick three tools you use",
            "Ask: does it learn from examples?",
            "Ask: does it make something new?",
            "Draw it in the smallest box that fits",
        ), said=(
            "Here's a way to practise. Pick three tools you use every week.",
            ("For each one, ask: did it learn from examples? If yes, it's at "
             "least machine learning."),
            "Then ask: does it make something new, like text or an image?",
            ("Place it in the smallest box that fits. Remember, every box sits "
             "inside the ones before."))),
        Scene("", "pause", "Pause and try this", items=(
            "Write down three tools you use",
            "Place each one in a box",
        ), said=(
            "Pause the video and write down three tools you use this week.",
            "Place each in a box, then resume and check yourself.")),
        Scene("", "quiz", "Claude writes you a reply. Smallest box that fits?", items=(
            "AI", "Deep learning", "Generative AI",
        ), answer=2, caption="It makes new text: generative AI, inside the rest.",
              said=(
            ("Last quiz. Claude writes you a brand new reply. What's the smallest "
             "box that fits? Comment."),
            ("C, generative AI. It makes new text, and it still sits inside all "
             "the bigger boxes."))),
        Scene("Recap", "checklist", "Recap", items=(
            "AI: the big box",
            "Machine learning: learns from examples",
            "Deep learning: many-layered networks",
            "Generative AI: makes new things",
        ), said=(
            "Recap. AI is the big box, machines doing smart things.",
            "Machine learning finds its own rules from examples.",
            "Deep learning is machine learning with many layers.",
            ("[happy] And generative AI makes new things. Four words, one picture, "
             "and you can place any tool."))),
    ),
)
