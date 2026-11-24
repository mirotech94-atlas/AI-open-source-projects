"""Local #4: Hermes Agent on a DGX Spark, talking to a model served by vLLM.

Study note: docs/academy-references/local_04.md

Written 2026-10-18 by the script writer (docs/academy-script-writer.md). No
live demo: no new recording can be made, so the picture is the commands on
screen, steps, bullets, a diagram, two quizzes and a pause.

Every fact was read on NVIDIA's own playbook pages on 2026-10-18:

  build.nvidia.com/spark/hermes-agent (Overview)
      (Hermes Agent "built by Nous Research", "runs as a terminal TUI";
      "Install Hermes on your hardware platform and connect it to a local
      LLM served by vLLM (the agent-ready nvidia/Qwen3.6-35B-A3B-NVFP4
      recipe)"; DGX Spark, DGX OS, 128 GB unified memory; prerequisites
      "Docker with the NVIDIA Container Toolkit, plus a HuggingFace token to
      download the model"; "Keep the vLLM endpoint bound to the hardware
      platform; do not forward http://<host-ip>:8000 to your LAN or the
      public internet without strong authentication"; "only place files on
      it that the agent is allowed to access"; "Periodically review Hermes
      session history")
  build.nvidia.com/spark/hermes-agent/instructions
      (Step 2: "a model server must be running before you launch the Hermes
      installer"; follow the "Serve LLMs with vLLM playbook's Agent-ready
      Models guidance to launch the server in a separate terminal"; "It
      serves an OpenAI-compatible API at http://localhost:8000/v1"; "Once
      the server reports Application startup complete";
      curl -sS http://localhost:8000/v1/models returns "a top-level data
      array listing the served model"; Step 3b: "choose Custom endpoint
      (enter URL manually)", API base URL http://localhost:8000/v1, "API key
      [optional] -- Leave blank ... vLLM does not require a key for a local
      model", pick the model the endpoint lists, context length "leave
      blank for auto-detect"; the non-interactive fallback: hermes config
      set model.provider custom, model.base_url http://localhost:8000/v1,
      model.default <handle>, hermes -z "Reply exactly HERMES_OK" "should
      return HERMES_OK"; Step 3d: run hermes, type hello; Step 5: "The
      Blank Slate install from Step 3 starts with the agent's tools
      disabled", hermes tools, "Toggle entries with SPACE and press ENTER")
  build.nvidia.com/spark/hermes-agent/troubleshooting
      (models not listed: "vLLM is not running yet or is still loading the
      checkpoint", watch for Application startup complete; Connection
      refused to localhost:8000: "docker ps, then curl
      http://localhost:8000/v1/models"; out of memory: "relaunch vLLM with a
      lower --gpu-memory-utilization / --max-model-len (or a smaller model
      handle) and re-point Hermes via hermes model")
  build.nvidia.com/spark/vllm
      ("an inference engine for serving large language models through an
      OpenAI-compatible API"; the recommended path uses NVIDIA Sync to
      start and stop the container)

The vLLM launch command itself sits in NVIDIA's recipe pages, so the script
points to the playbook rather than copying a command not read today. The
playbook installs Hermes from raw.githubusercontent.com/NousResearch/
hermes-agent/main/scripts/install.sh, a different address from the one
local #2 shows (hermes-agent.nousresearch.com/install.sh); the script names
neither and refers back to episode two. No speed, size or price is said.
"""
from __future__ import annotations

from ..tutorial_series import (
    LOCAL_PLANNED,
    Episode,
    Scene,
    payoff_scene,
    scripted,
)

CHECK = """curl -sS http://localhost:8000/v1/models     # vLLM is up: a "data" list
hermes config set model.provider custom
hermes config set model.base_url http://localhost:8000/v1
hermes config set model.default nvidia/Qwen3.6-35B-A3B-NVFP4
hermes -z "Reply exactly HERMES_OK"           # → HERMES_OK"""

PICTURE = (
    "vLLM\nserves the model locally",
    "localhost:8000/v1\nan OpenAI-style API",
    "Hermes Agent\nchats and uses tools",
)

EPISODE: Episode = scripted(
    next(e for e in LOCAL_PLANNED if e.number == 4),
    title_hook="Hermes Agent on DGX Spark with vLLM",
    links=(("Run Hermes Agent with a Local LLM (NVIDIA)",
            "https://build.nvidia.com/spark/hermes-agent"),
           ("Its instructions",
            "https://build.nvidia.com/spark/hermes-agent/instructions"),
           ("Its troubleshooting",
            "https://build.nvidia.com/spark/hermes-agent/troubleshooting"),
           ("Serve LLMs with vLLM (NVIDIA)", "https://build.nvidia.com/spark/vllm")),
    ask="What's the first job you'd give an agent that never leaves your desk? Comment.",
    topic_said=("Today: Hermes Agent on a DGX Spark, with the model served on the same "
                "machine by vLLM."),
    topic_shown="Hermes + vLLM, on one box",
    payoff=payoff_scene(
        ("By the end, Hermes answers you from a model running on your own Spark, and "
         "you'll know how to prove it."),
        "hermes -z \"Reply exactly HERMES_OK\" → HERMES_OK",
        items=PICTURE,
        caption="the model never leaves the box"),
    hook=Scene("What we'll build", "hook", "An agent, fully local", items=PICTURE,
               code=CHECK, caption="the model never leaves the box", said=(
                   ("In episode two, Hermes used a hosted model. [curious] What if the "
                    "model ran right here, on your own machine?"),
                   ("All you need is a DGX Spark, Docker with NVIDIA's container "
                    "toolkit, and a Hugging Face token to download the model."))),
    needs=("an NVIDIA DGX Spark", "Docker with the NVIDIA Container Toolkit",
           "a Hugging Face token for the model download"),
    scenes=(
        Scene("NVIDIA's playbook", "bullets", "Follow NVIDIA's playbook", items=(
            "build.nvidia.com/spark: Run Hermes Agent",
            "It uses vLLM, NVIDIA's agent-ready recipe",
            "Two terminals: one for vLLM, one for you",
        ), said=(
            ("We're following NVIDIA's own playbook for this, on build dot nvidia dot "
             "com, slash spark. It's called Run Hermes Agent with a Local LLM."),
            ("It serves the model with vLLM, an engine that offers an OpenAI style "
             "API, using the recipe NVIDIA lists as agent ready."),
            ("You'll want two terminals. vLLM keeps running in one. You work in the "
             "other."))),
        Scene("Step 1: serve the model", "steps", "Step 1: serve the model", items=(
            "Launch vLLM from the vLLM playbook",
            "Wait for: Application startup complete",
            "curl …/v1/models: your model in 'data'",
        ), said=(
            ("First, start vLLM, following the Serve LLMs with vLLM playbook. The "
             "model download needs your Hugging Face token. NVIDIA's recommended way "
             "starts and stops it from NVIDIA Sync, on your laptop."),
            ("Then wait. [emphasis] Nothing works until vLLM says Application "
             "startup complete."),
            ("Check it with curl on localhost, port eight thousand, slash v1, slash "
             "models. You should get a data list with your model in it."))),
        Scene("", "quiz", "Hermes can't list any models. Most likely?", items=(
            "Hermes is broken", "vLLM is still loading", "You need an API key",
        ), answer=1, caption="Wait for Application startup complete.", said=(
            ("Quick quiz. The Hermes setup can't find any models. What's most "
             "likely? Comment your answer."),
            ("B. vLLM hasn't finished loading. Watch its terminal for Application "
             "startup complete, then try again."))),
        Scene("Step 2: point Hermes at it", "code", "Point Hermes at localhost",
              code=CHECK, marks=((0, 1), (1, 4), (4, 5)),
              caption="the playbook's own commands", said=(
                  ("Now Hermes. Install it as in episode two. When setup asks for a "
                   "provider, choose Custom endpoint."),
                  ("Give it the base address, localhost, port eight thousand, slash "
                   "v1. Leave the API key blank: local vLLM doesn't need one. Then pick "
                   "your model."),
                  ("Prefer commands? These three config lines do the same. Then ask "
                   "it to reply exactly HERMES OK. [excited] If it says it back, "
                   "you're wired up."))),
        Scene("", "subscribe", "Running AI at home?", said=(
            "If you're running AI on your own hardware, subscribe for the rest.",)),
        Scene("Step 3: a first tool", "steps", "Step 3: give it a tool", items=(
            "Blank Slate starts with tools off",
            "hermes tools · SPACE to pick, ENTER",
            "Try it: ask it to read a local file",
        ), said=(
            ("The playbook's Blank Slate setup starts with every tool switched off. "
             "Safe, but not much use yet."),
            ("Run hermes tools. Pick file operations with the space bar, and press "
             "enter."),
            ("Now give it a small task, like summing up a text file in your home "
             "folder. Only put files there the agent may read."))),
        Scene("", "pause", "Pause and try this", items=(
            "Run the HERMES_OK check",
            "Turn on one tool, try one task",
        ), said=(
            "Pause the video and run the HERMES OK check from the screen.",
            "Then turn on one tool and give it one small task. Resume when it's done.")),
        Scene("Is it really local?", "diagram", "Keep it on the box", items=(
            "Endpoint: localhost:8000 only",
            "Never forward port 8000 to your network",
            "Review Hermes' session history",
        ), said=(
            ("How do you know it's local? Hermes talks to localhost, and that's the "
             "vLLM in your other terminal. Stop vLLM, and Hermes can't answer."),
            ("Keep it that way. NVIDIA's advice: don't forward port eight thousand to "
             "your network or the internet without strong authentication."),
            ("And now and then, read Hermes' session history, so you know what the "
             "agent has been doing."))),
        Scene("When it won't connect", "bullets", "Stuck?", items=(
            "Connection refused: docker ps, then curl",
            "Out of memory: lower --max-model-len",
            "Then re-point Hermes: hermes model",
        ), said=(
            ("[sighing] Connection refused? vLLM stopped, or it's still loading. Run "
             "docker ps to see the container, then the curl check."),
            ("Out of memory, or very slow? Relaunch vLLM with a lower max model "
             "length or memory share, or a smaller model."),
            ("Then point Hermes at it again with hermes model, and pick the model "
             "you're serving now. Same address, new model."))),
        Scene("", "quiz", "Share the endpoint with your whole network?", items=(
            "Sure, it's handy", "No, keep it on localhost",
        ), answer=1, caption="Port 8000 stays on the box.", said=(
            ("Last quiz. Should you forward port eight thousand to your office "
             "network? Comment."),
            ("B, no. Not without strong authentication. Keep the model's endpoint on "
             "the machine itself."))),
        Scene("Recap", "checklist", "Recap", items=(
            "vLLM serves the model on the Spark",
            "Check: curl …/v1/models",
            "Hermes: Custom endpoint, no key",
            "Port 8000 stays local",
        ), said=(
            "Recap. vLLM serves the model on the Spark, following NVIDIA's playbook.",
            "The curl check proves it's up, with your model in the list.",
            "Hermes points at the custom endpoint on localhost, with no key.",
            ("[happy] And port eight thousand stays on the box. Your agent, and your "
             "model, on your own desk."))),
    ),
)
