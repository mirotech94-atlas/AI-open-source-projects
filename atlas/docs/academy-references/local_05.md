# Study note: local_05

Topic searched: "ollama vs llama.cpp vs vllm" (2026-10-18). Written in our own words; no transcript is kept in the repo.

## Videos studied

- https://www.youtube.com/watch?v=2t9XrPcAiHg -- Alex Ziskind, 302,200 views, 14:40, read 2026-10-18. 198 words/min; first on-screen action at 65 s (transcript heuristic); 7 chapters.
- https://www.youtube.com/watch?v=0ujh7hfutq0 -- IBM Technology, 108,184 views, 10:36, read 2026-10-18. 160 words/min; first on-screen action at 95 s (transcript heuristic); 4 chapters.
- https://www.youtube.com/watch?v=qdPkA5mxLhg -- KodeKloud, 89,147 views, 15:17, read 2026-10-18. 147 words/min; first on-screen action at 16 s (transcript heuristic); 14 chapters.

## How they open

None of the three covers all five of our options. Alex Ziskind compares
llama.cpp and Ollama hands-on and only points at vLLM at the end; IBM
compares llama.cpp and vLLM on a whiteboard; KodeKloud is a vLLM lab that
lists other engines in passing. NIM and TensorRT-LLM get a name-check at
most, and only Ziskind's closing line mentions the DGX Spark.

Ziskind opens on the crowded field (many local stacks), then a news hook
(a new llama.cpp web page) and a teaser that one behaviour may change your
choice. IBM opens on three reasons to run locally and a promise to help
you choose. KodeKloud opens on a question every viewer has seen: why the
same model answers faster in one place than another.

## The order they explain things

- Ziskind: install the easy way, then build from source, find the binary,
  pick a quantized model on Hugging Face, start the server, tour its web
  page, compare Ollama's page and stats, then the concurrency test, then a
  pointer to vLLM for larger machines.
- IBM: history (the first open-weight models that home hardware could not
  hold), then llama.cpp's three tricks (quantization, a single model file,
  CPU support), then vLLM's (batching, KV cache paging, a small model
  drafting for a big one), then the shared API, then choosing by hardware
  and number of users.
- KodeKloud: concept first (tokens per second, the memory waste problem,
  paging), then a lab in eight tasks from a plain baseline to a server, a
  load test, tuning and a dashboard, with a quiz after the main ideas and a
  takeaway list at the end.

## Examples and analogies

- Four chats asking for a story at once: one engine queues them, the other
  answers all four together (Ziskind).
- One model in three file sizes, full precision down to four-bit (Ziskind).
- Rounding pi, for quantization; pancakes on a griddle, for batching (IBM).
- Operating-system memory pages, and a game of Tetris, for how vLLM stores
  the cache (KodeKloud).
- A made-up startup whose CEO sold a service to many users (KodeKloud).

## Beginner mistakes they address

- Building the CPU version when the machine has a GPU.
- Trusting a model's file name instead of checking its files and size.
- Expecting one chat engine to serve several users without queueing.
- Picking the high-throughput engine for a one-person laptop setup.
- Leaving context length and parallel requests untuned for the workload.
- Assuming the model alone sets the speed, not the engine serving it.

## Pacing

Measured above: 198, 160 and 147 words a minute. Ziskind is quick and
terminal-heavy, with a long mid-roll sponsor read. IBM is calm and drawn,
ten minutes with no screen work. KodeKloud is the slowest and reaches the
screen in seconds, but its lab half is mostly line numbers and values
read aloud. KodeKloud has 14 named chapters, the most useful to a
searcher; IBM's four are named by idea.

## What they do badly, and what we do better

- No one covers the five options a Spark owner can actually pick from. We
  name all five and the NVIDIA playbook for each.
- Speed numbers from one small machine or a tiny model are shown as if
  they apply everywhere. We state no speed and no ranking.
- Ziskind's verdict rests on one test on a Mac. We sort by the job you
  want done, not by a benchmark.
- KodeKloud reads fill-in-the-blank answers line by line. We stay on
  what each option is for, and what it asks of you.
- None says what each needs before you start (an account, a build, Python
  skills). We give each one's prerequisites from its own playbook.

## What we take (structure only)

1. Open on the confusion: several names that seem to do the same thing.
2. Say first what they all share (load a model, answer over an API).
3. Give each option the same three beats: what it is, the catch, the API.
4. A quiz that asks which one fits a stated job.
5. End on a decision map keyed to the job, not a winner.
6. A pause that sends the viewer to read one option's requirements.
