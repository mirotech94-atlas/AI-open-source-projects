# Three more series: AI Explained, AI Security, Where AI Is Going

These widen the channel from "how to build" to "how to understand and stay safe", which is the question most newcomers have before they'll touch a tool. Same system: result or question in the first three seconds, one moving thing at a time, mistakes left in, human pace. Each lesson pairs with a blog post of the same name on agency.storefront.test/academy/ so the written version ranks on Google while the video ranks on YouTube.

## AI Explained (concept lessons, 6–9 min each)

Concept lessons are the hardest to keep engaging, so every one is anchored to a live demonstration, not slides.

| # | Lesson | The result on screen at 0–3 s | Demo that carries it | Checkpoint |
|---|---|---|---|---|
| 1 | AI vs machine learning vs deep learning vs generative AI | The four nested boxes drawn on one card | Spam filter (ML) → face unlock (DL) → Claude writing a reply (GenAI), each a real screen | Viewer places three tools they use into the boxes |
| 2 | How a language model actually works (no math) | Claude completing a sentence one token at a time, visibly | Same prompt run three times; show the differences and why | "Predicts what a right answer looks like" stated in their words |
| 3 | Hallucinations: why confident and wrong | A fluent, wrong return policy next to the real one | Ask without the document, then with it (RAG) | Same question, grounded answer, source shown |
| 4 | Tokens, context windows and what you pay for | A real bill line and the token counter | Paste a long doc, watch the count; trim and compare | Viewer estimates one task's cost |
| 5 | Agents vs workflows vs chatbots | Three frames side by side doing the same job | n8n fixed workflow vs an agent choosing tools | Viewer names which their task needs |
| 6 | RAG vs fine-tuning: which one you actually need | Two cards: cost, time, result | Attach the policy PDF; answers improve immediately | "RAG first" with the one exception stated |
| 7 | What MCP is and why it saves you rebuilding everything | One connector used by two different models | Connect a sheet once, query from Claude and from n8n | Same data, two surfaces |
| 8 | Local models: what runs on your own machine | A model answering with Wi-Fi off | Local run vs cloud run, same question, timed | Viewer knows what their hardware can run |

## AI Security (7–10 min each; every lesson ends with a fix that is verified on camera)

| # | Lesson | The result on screen at 0–3 s | The attack shown (safely, on your own test setup) | The fix verified |
|---|---|---|---|---|
| 1 | Prompt injection, live | An agent about to forward email because a message told it to; the approval step stopping it | Hidden instruction in a test email to your own inbox | Content passed as labeled data; approval blocks the action |
| 2 | Least-privilege agents | Two credentials side by side: "everything" vs "one sheet, read only" | Agent with full scope deletes a test folder | Scoped key; same task succeeds, delete impossible |
| 3 | Where your data goes | The data-settings page of three tools | Paste test PII, show where it's stored/trained | Written one-paragraph policy; training opt-out toggled |
| 4 | Leaked keys and spending caps | A $20 cap saving a test account from a runaway loop | Key in an exported workflow JSON | Credential store; cap set; key rotated |
| 5 | Hallucinated facts in customer replies | Wrong price in a perfect-looking email | Reply drafted from memory | Grounded on the real price list; human read before send |
| 6 | Deepfake and voice-clone scams: protect your family | A cloned voice saying your name (your own, consented) | Walk through the scam script: urgency, secrecy, irreversible payment | Family code word; hang-up-and-call-back drill |
| 7 | The kill switch and the manual path | Workflow deactivated in one click | Simulated bad loop | One-page manual procedure shown |
| 8 | A security review of my own agent | The monthly review checklist | Sample of real actions ATLAS took | Each ticked or fixed on camera |

Rules for this series: demonstrate only against your own accounts and test data; never show a working attack on a third party; name the defense before the attack is fully shown; no fear music, no red flashing. The ErrorRecovery card is the visual language here — the real problem, the cause, the fix, verified.

## Where AI Is Going (5–7 min, one per month, opinion labeled as opinion)

| # | Lesson | Anchor |
|---|---|---|
| 1 | The model isn't the bottleneck any more | Same task, three models, nearly identical results; the difference was the input |
| 2 | From chat to agent, and why the approval step is the whole game | One goal, agent completes it with tools, pauses before sending |
| 3 | What to learn first (and what to skip) | The three-skill path from the blog post, built in one session |
| 4 | Judgment is the job now | Reviewing fifty drafts in ten minutes; what "right" looked like |
| 5 | Local AI for the sensitive stuff | Hermes Agent on the DGX Spark handling a document that never leaves the room |
| 6 | Trust as the product | "A real person checked this" shown as a visible step customers see |

## Tags for the new series (add to the channel tags on every upload)

**AI Explained:** `machine learning explained`, `deep learning explained`, `generative ai explained`, `ai vs machine learning vs deep learning`, `what is generative ai`, `how do large language models work`, `llm explained simply`, `ai for beginners`, `artificial intelligence explained`, `rag vs fine tuning`, `what is mcp`, `ai hallucination explained`

**AI Security:** `ai security for small business`, `prompt injection explained`, `prompt injection attack`, `ai agent security`, `how to protect yourself from ai scams`, `deepfake scam protection`, `voice cloning scam`, `ai data privacy`, `secure ai workflows n8n`, `api key security`, `least privilege ai agents`

**Where AI Is Going:** `future of ai`, `future of ai for small business`, `what to learn ai 2026`, `ai skills to learn`, `ai career advice`, `local ai future`, `ai agents future`

**Hashtags:** AI Explained `#machinelearning #deeplearning #generativeai` (+ `#artificialintelligence #ai`); AI Security `#aisecurity #cybersecurity #promptinjection` (+ `#deepfake #privacy`); Where AI Is Going `#futureofai #artificialintelligence #aiagents`.

## Thumbnail words (2–5, `chapter` type, same outcome as the title)

AI Explained: "4 boxes, one picture" · "Why it's confidently wrong" · "RAG or fine-tune?" — AI Security: "It read the email and obeyed" · "One key, one sheet" · "Hang up. Call back." — Future: "The model isn't the bottleneck" · "Learn this first".
