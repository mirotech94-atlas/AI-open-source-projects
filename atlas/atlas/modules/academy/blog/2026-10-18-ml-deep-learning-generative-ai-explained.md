---
title: Machine learning, deep learning and generative AI, explained the way I wish someone had explained it to me
slug: ml-deep-learning-generative-ai-explained
date: 2026-10-18
summary: Three terms that get used interchangeably and mean different things. One picture, no math, and the part that actually matters when you're deciding what to automate.
tags: [ai-explained, learning]
---

Every week someone asks me a version of the same question: "Is ChatGPT machine learning? Is it the same as AI? What's deep learning then?" The honest answer is that these are nested boxes, and once you see the boxes the rest of the vocabulary stops being scary.

## The boxes

```
Artificial intelligence
└── Machine learning          (learns rules from examples instead of being told the rules)
    └── Deep learning         (machine learning using large layered neural networks)
        └── Generative AI     (deep learning that produces new text, images, audio or code)
```

**Artificial intelligence** is the oldest and widest word. A chess program from 1997 that followed hand-written rules was AI. So is the spam filter in your inbox. The word just means "a computer doing something we'd call thinking if a person did it."

**Machine learning** is the part of AI where nobody writes the rules. You show the program thousands of examples — these emails are spam, these aren't — and it works out the pattern itself. That's it. It's statistics with a lot of examples. Your bank's fraud detection, the "you might also like" on a shopping site, the route Google Maps picks: machine learning.

**Deep learning** is machine learning done with a particular kind of model: a neural network with many layers. "Deep" means "many layers", nothing more mystical. The layers let the model learn messy things like what a cat looks like in a photo, or what a sentence means, where older methods stalled. Deep learning is why your phone recognizes your face and why speech-to-text finally works.

**Generative AI** is deep learning pointed at producing something new rather than labeling something existing. A spam filter says "spam / not spam". A generative model writes the email. Claude, ChatGPT, image generators and voice clones are all generative AI. Under the hood, a large language model is doing one thing: predicting the next word, over and over, extremely well, after reading a very large amount of text.

## The one idea that matters for your business

A model learns from examples. So it is only as good as the examples, and it has no idea when it's outside them.

That one sentence explains most of what you'll run into:

- Why a model sounds confident and is wrong — it predicts what a right answer *looks like*, which is not the same as checking.
- Why it does great on common tasks and badly on your weird edge case — the common task was in the examples.
- Why the same prompt gives different answers — prediction has randomness in it on purpose, so it doesn't repeat itself.
- Why "it worked in the demo" means little — the demo was a common case.

This is also why every workflow I ship pauses for a human before anything leaves the building. Not because the model is bad, but because it can't tell you when it's out of its depth, and you can.

## What you actually need to learn, in order

You don't need the math. You need to be able to answer four questions about any AI tool before you trust it with real work:

1. **What was it trained on, roughly?** General web text? Your company's documents? Nothing of yours yet?
2. **What does it get as input right now?** Just your prompt, or your files, your calendar, your inbox?
3. **What can it do with the output?** Show it to you, or send it, pay, delete, post?
4. **Where does it go wrong, and who catches that?** If the answer is "nobody", you have a problem waiting to happen.

The lessons on the channel are built around those questions. We build a real thing, feed it real input, name where it commonly goes wrong, and add the catch.

## Words you'll hear and what they mean in plain English

| Word | Plain meaning |
|---|---|
| Model | The trained program. "Claude" is a model. |
| Prompt | What you type or send to it. |
| Token | A chunk of a word. You're billed per token in and out. |
| Context window | How much the model can "see" at once — your prompt plus the files you attached. |
| Hallucination | A confident, fluent, wrong answer. |
| Fine-tuning | Training an existing model further on your own examples. Expensive; rarely needed. |
| RAG | Giving the model the right documents at question time instead of retraining it. This is what you usually want. |
| Agent | A model in a loop that can use tools (search, send email, run code) to finish a task. Powerful, and exactly where the pause-for-approval rule matters most. |
| MCP | A standard way to plug tools and data into a model, so you don't rebuild the connection for every app. |

## Where to go next

If you want the pictures and the live walkthrough, the *AI Agents Explained Simply* series on the channel covers this in short lessons. If you'd rather start building, the n8n series starts from zero and we add the safety rules as we go.

One more thing. Nobody who uses this stuff well memorized definitions. They built something small, watched it break, and fixed it. That's the whole method here.
