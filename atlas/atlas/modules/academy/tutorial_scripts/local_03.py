"""Local #3: NVIDIA's DGX Spark, and what its unified memory really means.

Written 2026-10-17 by the script writer (docs/academy-script-writer.md). No
live demo: no new recording can be made, so the picture is diagrams,
bullets, two commands on screen, two quizzes and a pause. It explains and
builds nothing, so it wears the Learn formula (title_formula=2).

Every fact and figure was read on NVIDIA's own pages on 2026-10-17:

  www.nvidia.com/en-us/products/workstations/dgx-spark/
      ("Powered by the NVIDIA GB10 Grace Blackwell Superchip"; "64 GB
      LPDDR5X* or 128 GB LPDDR5x, coherent unified system memory", the 64 GB
      one through OEM partners; 273 GB/s; "up to 1 petaFLOP of AI performance
      at FP4 precision"; up to 200B parameters with 128 GB; 20 Arm cores;
      NVIDIA DGX OS)
  docs.nvidia.com/dgx/dgx-spark/hardware.html
      ("128 GB LPDDR5x unified system memory, 256-bit interface, 4266 MHz,
      273 GB/s bandwidth"; "NVIDIA Grace Blackwell architecture with
      integrated GPU and CPU"; "Support for AI models up to 200 billion
      parameters")
  docs.nvidia.com/dgx/dgx-spark/known-issues.html
      ("On iGPU platforms, nvidia-smi will display 'Memory-Usage: Not
      Supported' even though per-process GPU memory is listed";
      cudaMemGetInfo's figure "may be smaller than the actual allocatable
      memory"; the buffer-cache flush: sudo sh -c 'sync; echo 3 >
      /proc/sys/vm/drop_caches', "After flushing the cache, restart your
      application")
  build.nvidia.com/spark (the playbook list: vllm, llama-cpp, trt-llm,
      nim-llm, hermes-agent among them) and build.nvidia.com/spark/vllm/troubleshooting
      ("On hardware platforms with unified memory, GPU and CPU share memory
      dynamically"; CUDA out of memory: "Context length too large / model
      too big", "Reduce --max-model-len and --max-num-seqs, or lower
      --gpu-memory-utilization"; "nvidia-smi --query-gpu memory fields report
      N/A")

No model is said to fit or not: the only size said is NVIDIA's own "up to"
figure, as NVIDIA words it.
"""
from __future__ import annotations

from ..tutorial_series import (
    LOCAL_PLANNED,
    Episode,
    Scene,
    payoff_scene,
    scripted,
)

COMMANDS = """nvidia-smi                 # Memory-Usage: Not Supported (expected)
sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'   # debugging only"""

PICTURE = (
    "One pool\n128 GB, shared",
    "CPU and GPU\nboth draw from it",
    "Weights + context\n+ everything else",
)

EPISODE: Episode = scripted(
    next(e for e in LOCAL_PLANNED if e.number == 3),
    title_hook="NVIDIA DGX Spark Unified Memory",
    title_formula=2,
    links=(("DGX Spark (NVIDIA)",
            "https://www.nvidia.com/en-us/products/workstations/dgx-spark/"),
           ("DGX Spark hardware overview",
            "https://docs.nvidia.com/dgx/dgx-spark/hardware.html"),
           ("DGX Spark known issues",
            "https://docs.nvidia.com/dgx/dgx-spark/known-issues.html"),
           ("DGX Spark playbooks", "https://build.nvidia.com/spark")),
    ask="What would you run first on a machine like this? Tell me in the comments.",
    topic_said=("Today: NVIDIA's DGX Spark, and what its unified memory really means "
                "for running models."),
    topic_shown="DGX Spark's unified memory",
    payoff=payoff_scene(
        ("By the end, you'll know why one shared pool of memory changes the question "
         "from how big is the GPU to what fills the pool."),
        "128 GB shared = model weights + context + the system",
        items=PICTURE,
        caption="one pool, many users"),
    hook=Scene("What you'll learn", "hook", "One pool of memory", items=PICTURE,
               code=("Usual PC: CPU memory + separate GPU memory\n"
                     "DGX Spark: one coherent pool, shared"),
               caption="one pool, many users", said=(
                   ("[curious] The DGX Spark's spec sheet says one hundred and twenty "
                    "eight gigabytes of unified memory. But what does unified mean?"),
                   ("Nothing to install today. All you need is a few minutes, and no "
                    "Spark of your own is required."))),
    needs=("nothing to install", "no DGX Spark needed to follow along"),
    scenes=(
        Scene("What it is", "bullets", "What the DGX Spark is", items=(
            "A small desktop AI computer from NVIDIA",
            "Powered by the GB10 Grace Blackwell Superchip",
            "CPU and GPU together, running DGX OS",
        ), said=(
            ("First, what is it? The DGX Spark is a small desktop computer from "
             "NVIDIA, made for AI work."),
            ("Inside is the GB10 Grace Blackwell Superchip: twenty Arm CPU cores and "
             "a Blackwell GPU, integrated together."),
            ("It runs NVIDIA's DGX OS. NVIDIA rates it at up to one petaflop of AI "
             "performance, at FP4 precision."))),
        Scene("Unified memory", "diagram", "Two pools, or one?", items=(
            "Usual PC\nCPU memory and GPU memory, apart",
            "DGX Spark\none coherent pool",
            "Shared\nCPU and GPU take what they need",
        ), said=(
            ("In a usual PC, the CPU has its memory, and the graphics card has its "
             "own, separate. A model must fit in the GPU's part."),
            ("[emphasis] The Spark has one pool instead: one hundred and twenty eight "
             "gigabytes of LPDDR5x, coherent, unified system memory."),
            ("NVIDIA's own troubleshooting puts it simply: the GPU and CPU share "
             "memory dynamically. Each takes what it needs, from the same pool."))),
        Scene("", "quiz", "On the Spark, who uses the 128 GB?", items=(
            "Only the GPU", "The CPU and GPU, shared", "Only the CPU",
        ), answer=1, caption="One pool, shared dynamically.", said=(
            ("Quick quiz. On the DGX Spark, who uses that memory: the GPU, the CPU, or "
             "both? Comment your answer."),
            "B, both. It's one pool, and they share it as they go.")),
        Scene("Where memory goes", "bullets", "Where the memory goes", items=(
            "The model's weights",
            "The context: long prompts and many chats",
            "The system and your other apps",
        ), said=(
            ("So where does it all go? First, the model's weights: the file that "
             "does the thinking has to sit in memory."),
            ("Second, the context. NVIDIA's vLLM troubleshooting lists out of memory "
             "as context length too large, or model too big. Long prompts cost memory "
             "too."),
            ("And third, everything else: the operating system and your apps live in "
             "that same pool. [sighing] So not all of it is there for weights."))),
        Scene("", "subscribe", "Still with me?", said=(
            "If you're curious about running AI on your own hardware, subscribe.",)),
        Scene("Size is half the answer", "bullets", "Why size is only half the answer", items=(
            "NVIDIA says: up to 200B parameters",
            "Up to, for a model, not for every setup",
            "Context and other apps change the room left",
        ), said=(
            ("NVIDIA says the Spark supports AI models up to two hundred billion "
             "parameters. Notice the words up to."),
            ("That's a ceiling for the model, not a promise for every setup you could "
             "build."),
            ("A long context, many chats at once, or other apps running all change "
             "how much room is left. Which is why we never guess."))),
        Scene("", "pause", "Pause and try this", items=(
            "Pick one thing you'd run",
            "List what else would share the memory",
        ), said=(
            "Pause the video and pick one thing you'd like to run on a machine like this.",
            ("Then list what else would share that memory while it runs. Resume when "
             "you have your list."))),
        Scene("Reading the numbers", "code", "Two things that surprise people",
              code=COMMANDS, marks=((0, 1), (1, 2)),
              caption="from NVIDIA's known issues", said=(
                  ("Two things surprise people. Run nvidia-smi, and memory usage says "
                   "not supported. That's expected: there's no separate GPU memory to "
                   "report."),
                  ("And if memory looks tight while you're well within capacity, NVIDIA "
                   "documents flushing the buffer cache, for debugging, then restarting "
                   "your app. [confident] It's a known quirk, not a broken machine."))),
        Scene("Check, never guess", "bullets", "Check, never guess", items=(
            "NVIDIA's playbooks: build.nvidia.com/spark",
            "vLLM, llama.cpp, TensorRT-LLM, NIM and more",
            "Out of memory? Shorter context, fewer chats",
        ), said=(
            ("So how do you know what works? NVIDIA publishes playbooks for the Spark, "
             "at build dot nvidia dot com slash spark."),
            ("There are playbooks for vLLM, llama dot cpp, TensorRT-LLM, NIM, and even "
             "Hermes Agent. We'll follow them, step by step, later in this series."),
            ("And if you hit out of memory, NVIDIA's fix is a shorter max context, "
             "fewer chats at once, or a lower memory share for the server."))),
        Scene("", "quiz", "A model is under 200B parameters. Does it fit?", items=(
            "Always", "Check the playbook: context and apps matter",
            "Never",
        ), answer=1, caption="'Up to' is a ceiling, not a promise.", said=(
            ("Last quiz. A model is under two hundred billion parameters. Is it "
             "guaranteed to run well? Comment."),
            ("B. Check NVIDIA's playbook. Context, other apps and settings all use the "
             "same pool."))),
        Scene("Recap", "checklist", "Recap", items=(
            "GB10: CPU and GPU on one chip",
            "One coherent 128 GB pool, shared",
            "Weights, context and the system all use it",
            "Check NVIDIA's playbooks, never guess",
        ), said=(
            "Recap. The GB10 puts the CPU and the GPU together.",
            "They share one coherent pool of memory, dynamically.",
            "Model weights, the context and the system all live in that pool.",
            ("[happy] So the real question is what fills the pool. And for that, check "
             "NVIDIA's playbooks, never a guess."))),
    ),
)
