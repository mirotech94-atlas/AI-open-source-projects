---
title: Where AI is going, and what's actually worth learning right now
slug: where-ai-is-going-what-to-learn
date: 2026-10-18
summary: No predictions about robots. What has changed in the last two years from where I sit, what I think holds, and the three skills I'd spend time on if I were starting today.
tags: [future, learning, ai-explained]
---

I don't do predictions. People who do mostly get paid for being confident, not for being right. But I do run a hosting company's infrastructure by day and a handful of automated businesses by night, and from that seat a few things have become clear enough that I'll build on them.

## What has changed

**Models got good enough that the model is no longer the bottleneck.** Two years ago the question was "can it do this?" Now it's "can I feed it the right information and catch it when it's wrong?" That shift moves the hard part from research labs to people like you and me, wiring things together.

**The unit of work moved from a chat to an agent.** A chat answers a question. An agent takes a goal, uses tools, and comes back with a result. Everything on this channel — n8n workflows, MCP, Claude with connectors — is about that move. It is also exactly where the risk lives, which is why the pause-for-approval rule keeps coming back in the lessons.

**Plumbing is being standardized.** MCP and similar standards mean a tool you connect once works across models. Last year I rebuilt the same Gmail connection three times. This year I built it once. Boring, and it's the thing that makes small-business automation actually affordable.

**Local models became usable.** You can run a capable model on a machine you own, with no data leaving the room. For anything sensitive this changes what's possible, and it's why the Local AI series runs on hardware I actually have on my desk.

**The scams arrived before the safeguards.** Voice cloning and deepfakes are already in the wild, aimed at families and finance teams. The defense is not technical; it's a code word and a habit of calling back.

## What I think holds

- **Judgment doesn't get automated; it gets more valuable.** The person who knows what a good customer reply looks like, what a fair price is, what a correct invoice contains — that person now reviews fifty of them an hour instead of writing five. The skill is knowing what "right" looks like.
- **Whoever owns the data and the process wins, not whoever owns the model.** Models are becoming interchangeable. Your customer history, your checklists, your way of doing things are not.
- **Trust becomes the product.** As generated content gets cheaper, "a real person checked this" becomes something people pay for. Build that in and say so.
- **The gap is between people who have tried and people who haven't.** Not between experts and beginners. Three weekends of building something small puts you ahead of most of the room.

## What I'd learn, in order, if I were starting today

**1. One automation tool, properly.** n8n is my pick because it's visual, self-hostable and honest about what each step does. Learn triggers, one API call, one branch, one wait-for-approval. Then build something you'll actually use, like the lead reply or booking reminder in the small-business series. Two weeks.

**2. How to talk to a model so it stays inside the lines.** System prompt sets the rules; user content is data; the model gets the real documents instead of guessing; the output has a fixed shape you can check. This is 80% of "prompt engineering" and it's an afternoon of practice, not a course.

**3. The security basics from the last post.** Scoped credentials, spending caps, what never goes in, where the off switch is. One hour to set up, and it's the difference between a tool and a liability.

After that, follow your actual work. If you sell things, learn how models write and check listings. If you run a team, learn standup bots and summaries. If you have parents online, teach them the code word this week.

What I would *not* spend time on first: the math behind neural networks, fine-tuning your own model, or chasing whichever model topped a benchmark this month. All of that is real, and none of it is where a small business or a learner gets value in year one.

## The thing I keep coming back to

Every lesson I make follows the same shape: show the real result, build it step by step, leave the mistake in, add the human check. I used to think that was a teaching style. I now think it's the whole philosophy. The future I'm building toward isn't one where AI runs my businesses. It's one where AI does the repetitive ninety percent and I make the calls that matter, with a report in my inbox instead of a fire.

If that's the version you want too, start with the free lessons, build one small thing, and email me when it breaks. That's when it gets interesting.
