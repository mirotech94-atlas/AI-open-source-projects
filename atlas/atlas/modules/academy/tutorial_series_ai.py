"""Three more series: AI Explained, AI Security, Where AI Is Going.

the owner 2026-10-18, video system pack
(docs/academy-video-system/series-ai-explained-and-security.md): the channel
widens from "how to build" to "how to understand and stay safe". Same system
as the others: the result or the question in the first three seconds, one
moving thing at a time, mistakes left in, human pace.

These are PLANS: every episode is an outline (the shape the scheduled writer
extends, docs/academy-script-writer.md), never a script. Each outline
carries, in its first three lines, the spec's result at 0-3 s, its demo
anchor and its checkpoint (AI Security: the fix verified on camera). The
thumbnail words are the spec's where it gives them; the others are drafts,
marked below. Tags are the episode's lesson-specific search phrases only:
the channel tags and the series set are added at upload
(tutorial_series.full_tags).

HOLDS: most of these lessons cannot be made from words, diagrams and code.
The spec anchors them on a live demonstration, a real bill, a timed run, an
attack on the owner's own test setup, or the owner himself (a consented clone of his
voice). The writer may not write demo scenes and may not invent a number
(invariant 6), so those episodes are held here, by (series, number), with a
plain reason. The writer skips them (docs/academy-script-writer.md), and
render and the daily lineup hold them (tutorial_series.held) until the
recording exists -- or, for a OWNER hold, until this mapping no longer lists
them. The writable ones are concept lessons whose result is a card or a
diagram and whose checkpoint is the viewer's own exercise.

Imported by tutorial_series at the end of its assembly, after `plan` exists,
the same way tutorial_scripts is.
"""
from __future__ import annotations

from .tutorial_series import plan

# The hold kinds. RECORDING lifts once the scripted episode shows a demo
# scene whose recording is on disk (tutorial_series.held); OWNER lifts only
# when the owner has recorded it and the entry is removed from HOLDS here.
RECORDING = "recording"
OWNER = "owner"

# The series whose every lesson is opinion: labelled so in the description
# (tutorials.description) and in the scope line (its OPENER_TOPICS).
OPINION_SERIES = ("future",)
OPINION_LINE = "Opinion: this lesson is an opinion, labelled as one. It is not a tutorial."
OPINION_TOPIC = "an opinion piece on "

# Rules for the AI Security series (the spec's "Rules for this series"),
# shown to the writer and re-read before any security episode is scripted.
SECURITY_RULES = (
    "Demonstrate only against our own accounts and test data.",
    "Never show a working attack on a third party.",
    "Name the defense before the attack is fully shown.",
    "No fear music, no red flashing.",
    ("The ErrorRecovery card is the visual language: the problem, the cause, the fix, "
    "verified."),
)

EXPLAINED_HASHTAGS = ("#machinelearning", "#deeplearning", "#generativeai")
SECURITY_HASHTAGS = ("#aisecurity", "#cybersecurity", "#promptinjection")
FUTURE_HASHTAGS = ("#futureofai", "#artificialintelligence", "#aiagents")

# ---------------------------------------------------------------- AI Explained
# Concept lessons, 6-9 min. Level 0 for #1-#4 (assumes nothing), 1 after.

EXPLAINED_PLANNED = (
    plan(1, 0, "ai-vs-machine-learning-vs-deep-learning",
         "AI vs Machine Learning vs Deep Learning vs Generative AI",
         "4 boxes,\none picture",
         ("The four words everyone mixes up, drawn as four nested boxes, with a "
          "tool you already use in each one."),
         ("ai vs ml", "what is deep learning", "ai types explained"),
         EXPLAINED_HASHTAGS,
         ("Result at 0-3 s: the four nested boxes drawn on one card",
          ("Demo anchor: spam filter (ML), face unlock (DL), Claude writing a reply "
           "(GenAI); taught as a diagram with one example per box"),
          "Checkpoint: the viewer places three tools they use into the boxes",
          "AI is the big box; machine learning learns rules from examples",
          "Deep learning: machine learning with many-layered networks",
          "Generative AI: models that make new text, images or sound",
          "Quiz: which box is a spam filter in?")),
    plan(2, 0, "how-a-language-model-works",
         "How a Language Model Actually Works (No Math)",
         "Token by\ntoken",
         ("What a large language model does when it answers: it predicts the next "
          "piece of text, one token at a time, which is why the same question can "
          "get different answers."),
         ("what is a token", "next token prediction", "llm basics"),
         EXPLAINED_HASHTAGS,
         ("Result at 0-3 s: Claude completing a sentence one token at a time, visibly",
          "Demo anchor: the same prompt run three times; the differences and why",
          ("Checkpoint: the viewer states \"predicts what a right answer looks like\" "
           "in their own words"),
          "Tokens: the pieces of text a model reads and writes",
          "Why the answers differ: it samples, it does not look things up",
          "Recap and a quiz")),
    plan(3, 0, "ai-hallucinations-explained",
         "Hallucinations: Why AI Is Confident and Wrong",
         "Why it's\nconfidently wrong",
         ("Why a language model can give a fluent, confident and wrong answer, and "
          "the fix: give it the real document and ask it to show the source."),
         ("why ai makes things up", "rag explained", "grounded ai answers"),
         EXPLAINED_HASHTAGS,
         ("Result at 0-3 s: a fluent, wrong return policy next to the real one",
          "Demo anchor: ask without the document, then with it (RAG)",
          "Checkpoint: same question, grounded answer, source shown",
          "Why fluent is not the same as true",
          "Grounding: the document goes in with the question",
          "Recap and a quiz")),
    plan(4, 0, "tokens-context-windows-cost",
         "Tokens, Context Windows and What You Pay For",
         "What you\npay for",
         ("What a token is, what a context window holds, and how both turn into "
          "the bill, read from a real bill line and a real token counter."),
         ("tokens explained", "context window", "ai api cost"),
         EXPLAINED_HASHTAGS,
         ("Result at 0-3 s: a real bill line and the token counter",
          "Demo anchor: paste a long document, watch the count; trim it and compare",
          "Checkpoint: the viewer estimates one task's cost",
          "Input tokens, output tokens, and the context window",
          "Trimming what you send: the cheapest fix",
          "Recap and a quiz")),
    plan(5, 1, "agents-vs-workflows-vs-chatbots",
         "Agents vs Workflows vs Chatbots",
         "Agent, workflow\nor chatbot?",
         ("Three ways to put AI to work, doing the same job side by side: a chatbot "
          "answers, a workflow follows fixed steps, an agent chooses its tools."),
         ("chatbot vs ai agent", "n8n agent", "ai workflow"),
         EXPLAINED_HASHTAGS,
         ("Result at 0-3 s: three frames side by side doing the same job",
          "Demo anchor: an n8n fixed workflow vs an agent choosing tools",
          "Checkpoint: the viewer names which one their task needs",
          "A chatbot: one question, one answer",
          "A workflow: the same steps every time",
          "An agent: picks the next step itself, so it needs limits")),
    plan(6, 1, "rag-vs-fine-tuning",
         "RAG vs Fine-Tuning: Which One You Actually Need",
         "RAG or\nfine-tune?",
         ("Two ways to make a model know your business: give it your documents at "
          "question time (RAG), or retrain it (fine-tuning). Start with RAG, and "
          "the one case where fine-tuning makes sense."),
         ("what is rag", "fine tuning explained", "rag for beginners"),
         EXPLAINED_HASHTAGS,
         ("Result at 0-3 s: two cards, cost, time and result, no figures guessed",
          ("Demo anchor: attach the policy PDF so answers improve; taught as steps, "
           "no improvement claimed that is not shown"),
          "Checkpoint: \"RAG first\", with the one exception stated",
          "RAG: the documents go in with the question",
          "Fine-tuning: the model itself is changed, slowly and at a cost",
          "Quiz: which one for a price list that changes every week?")),
    plan(7, 1, "what-is-mcp-explained",
         "What MCP Is and Why It Saves You Rebuilding Everything",
         "One connector,\nevery model",
         ("The Model Context Protocol in plain words: connect a tool once and use "
          "it from different AI apps, instead of building the same connection for "
          "each one."),
         ("mcp explained", "mcp for beginners", "mcp connector"),
         EXPLAINED_HASHTAGS,
         ("Result at 0-3 s: one connector used by two different models, as a diagram",
          ("Demo anchor: connect a sheet once, query it from Claude and from n8n; "
           "taught as the config and the steps"),
          "Checkpoint: same data, two surfaces",
          "Host, client, server: who does what",
          "Tools, resources and prompts",
          "Quiz: what do you rebuild when you switch apps?")),
    plan(8, 1, "local-models-explained",
         "Local Models: What Runs on Your Own Machine",
         "AI with\nWi-Fi off",
         ("What it means to run a model on your own computer, what your hardware "
          "can run, and a local answer timed against a cloud one."),
         ("offline ai", "run llm locally", "ai on your laptop"),
         EXPLAINED_HASHTAGS,
         ("Result at 0-3 s: a model answering with Wi-Fi off",
          "Demo anchor: a local run vs a cloud run, same question, timed",
          "Checkpoint: the viewer knows what their hardware can run",
          "Model size and memory: the first question",
          "Local vs cloud: privacy, cost, speed",
          "Recap and a quiz")),
)

# ----------------------------------------------------------------- AI Security
# 7-10 min each; every lesson ends with a fix verified on camera. Level 1.

SECURITY_PLANNED = (
    plan(1, 1, "prompt-injection-live",
         "Prompt Injection, Live: When an AI Agent Obeys an Email",
         # spec: "It read the email and obeyed" (six words, over the 2-5 the
         # spec's own thumbnail rule allows): a draft until the owner picks.
         "It obeyed\nthe email",
         ("A hidden instruction in an email tells an AI agent to forward your mail. "
          "The defense first: content passed as labelled data, and an approval "
          "step that stops the action. Shown on our own test inbox only."),
         ("prompt injection demo", "ai agent approval step", "untrusted input"),
         SECURITY_HASHTAGS,
         (("Result at 0-3 s: an agent about to forward email because a message told it "
          "to; the approval step stopping it"),
          ("Attack (own test setup only): a hidden instruction in a test email to our "
          "own inbox; the defense named first"),
          "Fix verified: content passed as labelled data; approval blocks the action",
          "What prompt injection is, in one sentence",
          "Why an agent cannot tell data from orders on its own",
          "Recap: label the data, approve the action")),
    plan(2, 1, "least-privilege-ai-agents",
         "Least-Privilege AI Agents: One Key, One Sheet",
         "One key,\none sheet",
         ("Give an agent only the access its job needs. A full-access key next to a "
          "read-only key for one sheet, and what each lets go wrong, on test data."),
         ("scoped api key", "read only access", "agent permissions"),
         SECURITY_HASHTAGS,
         (("Result at 0-3 s: two credentials side by side, \"everything\" vs \"one "
          "sheet, read only\""),
          "Attack (own test setup only): an agent with full scope deletes a test folder",
          "Fix verified: a scoped key; the same task succeeds, the delete is impossible",
          "Least privilege in one sentence",
          "Reading a credential's scope before you paste it",
          "Recap checklist")),
    plan(3, 1, "where-your-ai-data-goes",
         "Where Your Data Goes When You Use AI Tools",
         "Where your\ndata goes",
         ("The data settings of three AI tools, what they keep and what they train "
          "on, read on their own pages, and a one-paragraph policy you can adopt."),
         ("ai privacy settings", "ai training opt out", "data retention ai"),
         SECURITY_HASHTAGS,
         ("Result at 0-3 s: the data-settings page of three tools",
          ("Attack (own test setup only): paste test PII; show where it is stored or "
          "trained on, from the tools' own pages"),
          "Fix verified: a written one-paragraph policy; training opt-out toggled",
          "Stored, trained on, shared: three different things",
          "What never goes into a prompt",
          "Recap checklist")),
    plan(4, 1, "leaked-api-keys-spending-caps",
         "Leaked API Keys and Spending Caps",
         "Cap it\nfirst",
         ("How an API key leaks (an exported workflow is the common way), and the "
          "three fixes: a credential store, a spending cap, and rotating the key."),
         ("api key leak", "spending limit api", "rotate api key"),
         SECURITY_HASHTAGS,
         ("Result at 0-3 s: a cap saving a test account from a runaway loop",
          "Attack (own test setup only): a key left in an exported workflow JSON",
          "Fix verified: credential store; cap set; key rotated",
          "Where keys hide: exports, screenshots, chat",
          "Caps and alerts, set before anything runs",
          "Recap checklist")),
    plan(5, 1, "ai-hallucinated-customer-replies",
         "Hallucinated Facts in Customer Replies",
         "Wrong price,\nperfect email",
         ("An AI-drafted customer email that looks perfect and quotes the wrong "
          "price. The fix: ground it on the real price list, and a person reads it "
          "before it is sent."),
         ("ai customer replies", "human in the loop", "ai email mistakes"),
         SECURITY_HASHTAGS,
         ("Result at 0-3 s: a wrong price in a perfect-looking email",
          "Attack (own test setup only): a reply drafted from memory",
          "Fix verified: grounded on the real price list; a human reads it before send",
          "Why a model guesses a number",
          "Grounding and the read-before-send step",
          "Recap checklist")),
    plan(6, 1, "voice-clone-scams-family",
         "Deepfake and Voice-Clone Scams: Protect Your Family",
         "Hang up.\nCall back.",
         ("How voice-clone scams work (urgency, secrecy, a payment you cannot undo) "
          "and the two habits that beat them: a family code word, and hang up and "
          "call back."),
         ("voice clone scam", "family code word", "deepfake call"),
         SECURITY_HASHTAGS,
         ("Result at 0-3 s: a cloned voice saying your name (the owner's own, consented)",
          ("Attack (own test setup only): walk through the scam script: urgency, "
          "secrecy, irreversible payment; the defense named first"),
          "Fix verified: a family code word; the hang-up-and-call-back drill",
          "The three signs: urgency, secrecy, a payment you cannot undo",
          "The code word: agreed in person, never sent in a message",
          "Recap checklist")),
    plan(7, 1, "ai-workflow-kill-switch",
         "The Kill Switch and the Manual Path",
         "One click\nto stop",
         ("Every automation needs an off switch and a way to do the job by hand. "
          "Stopping a bad loop in one click, and the one-page manual procedure."),
         ("deactivate n8n workflow", "automation kill switch", "manual fallback"),
         SECURITY_HASHTAGS,
         ("Result at 0-3 s: a workflow deactivated in one click",
          "Attack (own test setup only): a simulated bad loop",
          "Fix verified: the one-page manual procedure shown",
          "Where the off switch is, before you need it",
          "Writing the manual path on one page",
          "Recap checklist")),
    plan(8, 1, "ai-agent-security-review",
         "A Security Review of My Own Agent",
         "The monthly\nreview",
         ("The monthly security review run on our own agent, ATLAS: a sample of "
          "the real actions it took, each ticked or fixed on camera."),
         ("ai security review", "agent audit checklist", "ai action log"),
         SECURITY_HASHTAGS,
         ("Result at 0-3 s: the monthly review checklist",
          "Attack (own test setup only): a sample of real actions ATLAS took",
          "Fix verified: each one ticked or fixed on camera",
          "What to look for in an action log",
          "Approvals, scopes, caps: the checklist",
          "Recap checklist")),
)

# ----------------------------------------------------------- Where AI Is Going
# 5-7 min, one per month, opinion labelled as opinion. Level 1.

FUTURE_PLANNED = (
    plan(1, 1, "model-isnt-the-bottleneck",
         "The Model Isn't the Bottleneck Any More",
         "The model isn't\nthe bottleneck",
         ("An opinion: the difference between AI results now comes from what you "
          "give the model, not which model you pick."),
         ("best ai model", "ai input quality", "prompt context"),
         FUTURE_HASHTAGS,
         ("Result at 0-3 s: the same task, three models, nearly identical results",
          "Anchor: the difference was the input, shown side by side",
          "Checkpoint: the viewer improves one input of their own",
          "Opinion, labelled: why the input matters more now",
          "What a good input has in it",
          "Recap and a question for the comments")),
    plan(2, 1, "chat-to-agent-approval-step",
         "From Chat to Agent: Why the Approval Step Is the Whole Game",
         "Approval is\nthe game",
         ("An opinion: as AI moves from answering to doing, the step where a person "
          "approves the action is what makes it safe to use."),
         ("human approval ai", "chat vs agent", "ai approval step"),
         FUTURE_HASHTAGS,
         (("Result at 0-3 s: one goal; the agent completes it with tools, pauses "
          "before sending"),
          "Anchor: the pause before sending, shown live",
          "Checkpoint: the viewer names the one action they would always approve",
          "Opinion, labelled: from chat to agent",
          "Why the approval step carries the trust",
          "Recap and a question for the comments")),
    plan(3, 1, "what-to-learn-first-ai",
         "What to Learn First in AI (and What to Skip)",
         "Learn this\nfirst",
         ("An opinion: the three skills worth learning first, built in one session, "
          "and what to skip for now."),
         ("learn ai first", "ai learning path", "ai skills beginners"),
         FUTURE_HASHTAGS,
         ("Result at 0-3 s: the three-skill path from the blog post",
          "Anchor: the path built in one session",
          "Checkpoint: the viewer picks their first skill",
          "Opinion, labelled: what to learn first",
          "What to skip for now, and why",
          "Recap and a question for the comments")),
    plan(4, 1, "judgment-is-the-job",
         "Judgment Is the Job Now",
         "Judgment is\nthe job",
         ("An opinion: when AI drafts the work, deciding what is right becomes the "
          "job. Reviewing a batch of drafts, and what \"right\" looked like."),
         ("ai and jobs", "reviewing ai output", "ai judgment skills"),
         FUTURE_HASHTAGS,
         ("Result at 0-3 s: reviewing fifty drafts in ten minutes",
          "Anchor: what \"right\" looked like in that review",
          "Checkpoint: the viewer writes their own one-line test for \"right\"",
          "Opinion, labelled: the job moves to judgment",
          "A review checklist you can reuse",
          "Recap and a question for the comments")),
    plan(5, 1, "local-ai-sensitive-documents",
         "Local AI for the Sensitive Stuff",
         "Never leaves\nthe room",
         ("An opinion: sensitive documents belong on a model you run yourself. "
          "Hermes Agent on the DGX Spark handling a document that never leaves the "
          "room."),
         ("local ai privacy", "dgx spark", "private documents ai"),
         FUTURE_HASHTAGS,
         ("Result at 0-3 s: Hermes Agent on the DGX Spark handling a document",
          "Anchor: the document never leaves the room, shown on the machine",
          "Checkpoint: the viewer sorts their documents into cloud and local",
          "Opinion, labelled: local for the sensitive stuff",
          "What counts as sensitive",
          "Recap and a question for the comments")),
    plan(6, 1, "trust-as-the-product",
         "Trust as the Product",
         "A person\nchecked this",
         ("An opinion: when anyone can generate anything, a visible human check is "
          "what customers pay for."),
         ("ai trust", "human in the loop ai", "ai customer trust"),
         FUTURE_HASHTAGS,
         ("Result at 0-3 s: \"A real person checked this\" as a visible step",
          "Anchor: the step customers see, drawn as a diagram of the flow",
          "Checkpoint: the viewer names where their customers would see it",
          "Opinion, labelled: trust as the product",
          "Making the human check visible",
          "Recap and a question for the comments")),
)

# The spec's thumbnail words, verbatim, where it gives them; every other
# thumb_title above is a draft.
SPEC_THUMB_WORDS: dict[tuple[str, int], str] = {
    ("explained", 1): "4 boxes, one picture",
    ("explained", 3): "Why it's confidently wrong",
    ("explained", 6): "RAG or fine-tune?",
    ("security", 1): "It read the email and obeyed",
    ("security", 2): "One key, one sheet",
    ("security", 6): "Hang up. Call back.",
    ("future", 1): "The model isn't the bottleneck",
    ("future", 3): "Learn this first",
}

# What the opener says "today we'll be learning about" (OPENER_TOPICS), every
# episode, no numbers; Where AI Is Going's say it is opinion (the scope line).
OPENER_TOPICS: dict[tuple[str, int], str] = {
    ("explained", 1): "AI, machine learning, deep learning and generative AI, told apart",
    ("explained", 2): "how a language model actually writes its answer",
    ("explained", 3): "why AI can be confident and wrong, and the fix",
    ("explained", 4): "tokens, context windows and what you actually pay for",
    ("explained", 5): "agents, workflows and chatbots, and which one your task needs",
    ("explained", 6): "RAG and fine-tuning, and which one you actually need",
    ("explained", 7): "what MCP is, and why it saves you rebuilding everything",
    ("explained", 8): "local models, and what runs on your own machine",
    ("security", 1): "prompt injection, and the approval step that stops it",
    ("security", 2): "giving an AI agent only the access it needs",
    ("security", 3): "where your data goes when you use AI tools",
    ("security", 4): "keeping API keys safe, and capping what they can spend",
    ("security", 5): "stopping made-up facts in your customer replies",
    ("security", 6): "voice-clone scams, and how to protect your family",
    ("security", 7): "the kill switch, and the manual path behind it",
    ("security", 8): "a security review of our own AI agent",
    ("future", 1): OPINION_TOPIC + "why the model isn't the bottleneck any more",
    ("future", 2): OPINION_TOPIC + "why the approval step is the whole game",
    ("future", 3): OPINION_TOPIC + "what to learn first in AI",
    ("future", 4): OPINION_TOPIC + "why judgment is the job now",
    ("future", 5): OPINION_TOPIC + "local AI for sensitive documents",
    ("future", 6): OPINION_TOPIC + "trust as the product",
}

_REC = "needs a screen recording of "
HOLDS: dict[tuple[str, int], tuple[str, str]] = {
    ("explained", 2): (RECORDING, _REC + "Claude answering one token at a time, "
                       "the same prompt run three times"),
    ("explained", 3): (RECORDING, _REC + "a real wrong answer next to the real "
                       "policy, then the grounded answer"),
    ("explained", 4): (RECORDING, _REC + "a real bill line and token counter; no "
                       "number may be guessed"),
    ("explained", 5): (RECORDING, _REC + "the three approaches doing the same job"),
    ("explained", 8): (RECORDING, _REC + "a local model answering with Wi-Fi off, "
                       "timed against a cloud run"),
    ("security", 1): (RECORDING, _REC + "the injection on the owner's own test inbox, "
                      "the approval step stopping it"),
    ("security", 2): (RECORDING, _REC + "a full-scope test key deleting a test "
                      "folder, then the scoped key failing to"),
    ("security", 3): (RECORDING, _REC + "the three tools' data settings and the "
                      "opt-out toggled, on test data"),
    ("security", 4): (RECORDING, _REC + "a cap stopping a test loop and the key "
                      "rotated, on a test account"),
    ("security", 5): (RECORDING, _REC + "the drafted reply and the grounded fix "
                      "verified on camera"),
    ("security", 6): (OWNER, ("needs the owner: a clone of his own voice, with his "
                      "consent, recorded by him")),
    ("security", 7): (RECORDING, _REC + "the simulated loop and the one-click stop"),
    ("security", 8): (OWNER, ("needs the owner: his review of real ATLAS actions, each "
                      "ticked or fixed on camera")),
    ("future", 1): (RECORDING, _REC + "the same task run on three models"),
    ("future", 2): (RECORDING, _REC + "an agent completing a goal and pausing "
                    "before it sends"),
    ("future", 3): (RECORDING, _REC + "the three-skill path built in one session"),
    ("future", 4): (RECORDING, _REC + "the real review of a batch of drafts; no "
                    "count may be guessed"),
    ("future", 5): (RECORDING, _REC + "Hermes Agent on the owner's DGX Spark"),
}
