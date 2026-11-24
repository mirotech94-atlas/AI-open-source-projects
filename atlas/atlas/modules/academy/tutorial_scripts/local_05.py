"""Local #5: five ways to serve a model on a DGX Spark, and how to pick one.

Study note: docs/academy-references/local_05.md

Written 2026-10-18 by the script writer (docs/academy-script-writer.md). No
live demo: no new recording can be made, so the picture is bullets, a
diagram, two quizzes and a pause. It compares and builds nothing.

Every fact was read on NVIDIA's own DGX Spark playbooks on 2026-10-18:

  build.nvidia.com/spark/open-webui ("Chat with LLMs Using Open WebUI and
      Ollama": "a self-hosted chat application that you can run entirely
      on your hardware platform"; "browser based"; "an Open WebUI container
      image with Ollama", "integrated Ollama for model management"; "use
      the Open WebUI browser interface to download and run a model")
  build.nvidia.com/spark/llama-cpp ("a lightweight C/C++ inference stack";
      "You build it with CUDA"; "load GGUF weights and expose chat through
      llama-server's OpenAI-compatible HTTP API"; "no separate Python
      inference framework required"; "Understanding of git and building
      from source with CMake"; "about 30 GB free for the example model")
  build.nvidia.com/spark/vllm ("a highly performant inference engine for
      serving large language models through an OpenAI-compatible API. It
      uses efficient memory management and continuous batching to increase
      serving throughput")
  build.nvidia.com/spark/hermes-agent (Hermes connects "to a local LLM
      served by vLLM (the agent-ready ... recipe)")
  build.nvidia.com/spark/nim-llm ("NVIDIA NIM is containerized software for
      fast, reliable AI model serving"; "authenticate with NVIDIA's
      registry, launch the NIM inference microservice, and validate the
      OpenAI-compatible HTTP endpoint"; "NGC account with an API key")
  build.nvidia.com/spark/trt-llm ("an open-source library for optimizing
      and accelerating large language model (LLM) inference on NVIDIA
      GPUs"; "lower latency and higher throughput"; before starting:
      "Python proficiency and experience with PyTorch", "Experience with
      inference servers and containerized environments"; ports for
      "OpenAI-compatible serving"; a Model Support Matrix "supported with
      TensorRT-LLM on Spark")

Each playbook names its own example model and what it needs, so the only
size said is llama.cpp's own "about 30 GB free", as NVIDIA words it. The
Open WebUI page doesn't call Ollama OpenAI-compatible, so the script
doesn't either. No speed figure or ranking is claimed.
"""
from __future__ import annotations

from ..tutorial_series import (
    LOCAL_PLANNED,
    Episode,
    Scene,
    payoff_scene,
    scripted,
)

PICTURE = (
    "Ollama · llama.cpp\nstart simple",
    "vLLM\nserve your agents",
    "NIM · TensorRT-LLM\nNVIDIA's tuned paths",
)

EPISODE: Episode = scripted(
    next(e for e in LOCAL_PLANNED if e.number == 5),
    title_hook="DGX Spark's Five Ways to Serve a Model",
    links=(("DGX Spark playbooks (NVIDIA)", "https://build.nvidia.com/spark"),
           ("Open WebUI and Ollama", "https://build.nvidia.com/spark/open-webui"),
           ("llama.cpp", "https://build.nvidia.com/spark/llama-cpp"),
           ("vLLM", "https://build.nvidia.com/spark/vllm"),
           ("NIM for LLMs", "https://build.nvidia.com/spark/nim-llm"),
           ("TensorRT-LLM", "https://build.nvidia.com/spark/trt-llm")),
    ask="Which of the five would you try first, and for what? Tell me below.",
    topic_said=("Today: five ways to serve a model on a DGX Spark, what each one is for, "
                "and how to choose."),
    topic_shown="Five ways to serve a model",
    payoff=payoff_scene(
        ("By the end, you'll know which of the five fits your job, and which NVIDIA "
         "playbook to open first."),
        "Browser chat → Ollama · agents → vLLM · tuning → TensorRT-LLM",
        items=PICTURE,
        caption="pick by the job, not the hype"),
    hook=Scene("What you'll learn", "hook", "Five engines, one Spark", items=PICTURE,
               caption="pick by the job, not the hype", said=(
                   ("[sighing] Ollama, llama.cpp, vLLM, NIM, TensorRT. Five names, and "
                    "they all seem to do the same thing."),
                   ("They don't, quite. Nothing to install today: we're comparing them, "
                    "straight from NVIDIA's own playbooks."))),
    needs=("nothing to install", "no DGX Spark needed to follow along"),
    scenes=(
        Scene("One job, five ways", "bullets", "What they all do", items=(
            "Load a model, answer requests",
            "Most speak an OpenAI-style API",
            "Each has its own NVIDIA playbook",
        ), said=(
            ("First, what they share. Each one loads a model onto your Spark, and "
             "answers requests to it."),
            ("Four of the five offer an OpenAI style API, so apps and agents can talk "
             "to them the same way."),
            ("And NVIDIA has a playbook for each, on build dot nvidia dot com, slash "
             "spark. That's our only source today."))),
        Scene("Ollama", "bullets", "Ollama, with Open WebUI", items=(
            "Chat in your browser",
            "Download models from the web page",
            "One container: Open WebUI + Ollama",
        ), said=(
            ("Ollama comes in NVIDIA's Open WebUI playbook. You chat in a browser, "
             "from your laptop or on the Spark itself."),
            ("You download and run models right from that web page. Ollama handles "
             "the model management."),
            ("It's one container with both inside. [confident] The gentlest start "
             "of the five."))),
        Scene("llama.cpp", "bullets", "llama.cpp", items=(
            "Light, written in C and C++",
            "You build it with CUDA, from source",
            "GGUF models · llama-server API",
        ), said=(
            ("llama.cpp is a lightweight stack, written in C and C plus plus, with no "
             "separate Python framework."),
            ("The catch? You build it yourself, with CUDA, using git and CMake."),
            ("It loads models in the GGUF format, and llama server gives you that "
             "OpenAI style API."))),
        Scene("", "quiz", "You want to chat in a browser, no building. Pick…", items=(
            "llama.cpp", "Ollama with Open WebUI", "TensorRT-LLM",
        ), answer=1, caption="Open WebUI + Ollama: chat and models in the browser.",
              said=(
            ("Quick quiz. You want to chat with a local model in a browser, and build "
             "nothing. Which one? Comment your answer."),
            "B. Ollama with Open WebUI. Models and chat, all in the web page.")),
        Scene("vLLM", "bullets", "vLLM", items=(
            "A high-performance serving engine",
            "Continuous batching: many requests",
            "The one the Hermes playbook uses",
        ), said=(
            ("vLLM is built for serving. NVIDIA calls it a highly performant "
             "inference engine."),
            ("It uses continuous batching to handle lots of requests, which matters "
             "once apps and agents share one model."),
            ("[emphasis] And it's the one NVIDIA's Hermes Agent playbook uses. That's "
             "what we set up last time."))),
        Scene("", "subscribe", "Still with me?", said=(
            "If you're building a local AI setup, subscribe for the rest of the series.",)),
        Scene("NIM", "bullets", "NVIDIA NIM", items=(
            "Ready-made containers from NVIDIA",
            "Needs an NGC account and API key",
            "OpenAI-compatible endpoint",
        ), said=(
            ("NIM is NVIDIA's packaged option: ready-made containers for serving a "
             "model."),
            ("You sign in to NVIDIA's registry first, with an NGC account and its key. "
             "Keep that key private."),
            "Then you launch the container, and check its OpenAI compatible endpoint.")),
        Scene("TensorRT-LLM", "bullets", "TensorRT-LLM", items=(
            "NVIDIA's open-source optimiser",
            "Aimed at lower latency, higher throughput",
            "For people at home in Python and PyTorch",
        ), said=(
            ("TensorRT LLM is an open source library from NVIDIA, for optimising "
             "and speeding up models on its GPUs."),
            ("NVIDIA aims it at lower latency and higher throughput."),
            ("But the playbook expects Python and PyTorch experience. It's the most "
             "hands-on of the five."))),
        Scene("", "pause", "Pause and try this", items=(
            "Open build.nvidia.com/spark",
            "Read one playbook's prerequisites",
        ), said=(
            "Pause the video and open NVIDIA's Spark playbooks page.",
            ("Pick the one that fits your job, and read its prerequisites. Resume when "
             "you know what it needs."))),
        Scene("Pick by the job", "diagram", "Pick by the job", items=(
            "Chat in a browser\nOllama + Open WebUI",
            "Agents and apps\nvLLM, or NIM",
            "Squeeze it further\nTensorRT-LLM",
        ), said=(
            ("So, how to pick? To chat in a browser, start with Ollama and Open "
             "WebUI."),
            ("For agents and apps calling one model, vLLM, or NIM if you'd like "
             "NVIDIA's ready-made containers."),
            ("And to squeeze out more, with Python skills to match, TensorRT LLM. "
             "llama.cpp fits when you like to build things yourself."))),
        Scene("Will it fit?", "bullets", "Will my model fit?", items=(
            "Each playbook names a tested model",
            "And what it needs: memory, disk",
            "No guessing: check the recipe",
        ), said=(
            ("Will your favourite model fit? Don't guess. Each playbook names the "
             "models it was tested with."),
            ("And it says what they need. llama.cpp's, for example, asks for about "
             "thirty gigabytes free for its example model."),
            "If a model isn't in a recipe, treat it as untested.")),
        Scene("", "quiz", "Hermes and two apps share one model. Pick…", items=(
            "Ollama in a browser", "vLLM", "Whichever's smallest",
        ), answer=1, caption="vLLM's continuous batching serves many requests.", said=(
            ("Last quiz. Your agent and two apps all call the same local model. "
             "Which fits best? Comment."),
            "B, vLLM. It's built to serve many requests at once.")),
        Scene("Recap", "checklist", "Recap", items=(
            "Ollama + Open WebUI: browser chat",
            "llama.cpp: light, build it yourself",
            "vLLM or NIM: serve apps and agents",
            "TensorRT-LLM: tuning, for experts",
        ), said=(
            "Recap. Ollama with Open WebUI is the easy way to chat in a browser.",
            "llama.cpp is light, if you're happy to build it.",
            "vLLM, or NVIDIA's NIM, serve apps and agents through one API.",
            ("[happy] And TensorRT LLM is for tuning, if you know Python well. Pick by "
             "the job, and follow the playbook."))),
    ),
)
