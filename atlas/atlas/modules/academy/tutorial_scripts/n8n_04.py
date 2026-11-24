"""n8n #4: handling errors, so one failed step doesn't stop everything.

Written 2026-10-17 by the script writer (docs/academy-script-writer.md). No
live demo: no new recording can be made, so the picture is steps, bullets,
a diagram, two quizzes and a lab.

Every product fact was read on the official n8n docs on 2026-10-17:

  docs.n8n.io/build/understand-workflows/workflow-components/work-with-nodes.md
      (node settings: Retry On Fail, "When an execution fails, the node
      reruns until it succeeds"; On Error: Stop Workflow "Halts the entire
      workflow when an error occurs", Continue "Proceeds to the next node
      despite the error, using the last valid data", Continue (using error
      output) "Continues workflow execution, passing error information to
      the next node for potential handling")
  docs.n8n.io/build/flow-logic/handle-errors-gracefully.md
      ("For each workflow, you can set an error workflow in Workflow
      Settings"; "It runs if an execution fails"; "The error workflow must
      start with the Error Trigger"; the Stop And Error node "to force
      executions to fail under your chosen circumstances")
  docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.errortrigger.md
      ("When another linked workflow fails, this node gets details about
      the failed workflow and the errors"; Options > Settings > Error
      workflow; "If a workflow uses the Error Trigger node, you don't have
      to publish the workflow"; "You can't test error workflows when
      running workflows manually"; "The Error Trigger only runs when an
      automatic workflow errors")
  docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.stopanderror.md
      ("display custom error messages, cause executions to fail under
      certain conditions")
  docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.slack.md
      (Message: "Send a message"; the Slack credential page for the app)

Max Tries and Wait Between Tries are not described on the docs read today,
so no retry count or wait is said. The lab's HTTP Request calls a
misspelled Open-Meteo path on purpose: on 2026-10-17
geocoding-api.open-meteo.com/v1/serch returned 404 and /v1/search 200
(curl), so the node fails until the viewer fixes it. The lab uses only the
Manual Trigger, HTTP Request and Edit Fields (Set v3.4), no credentials.

Added 2026-10-25 (the owner): the one failure Retry On Fail, On Error and an
error workflow do not catch -- a workflow that never starts (its trigger
stopped, or it was turned off) fails nothing, so nothing fires. The answer
taught is a silent-failure watch, our own build: a second workflow on a
schedule asks the n8n public API for the watched workflow's last execution
(GET /api/v1/executions?workflowId=<id>&limit=1, the n8n API credential),
alerts once when it is older than a set age, stays quiet while nothing
changes (the state in $getWorkflowStaticData('global'), which n8n keeps on
automatic runs only, not test clicks) and says once when it is back. These
facts were written from the n8n public API and Code node references as
known, not read again on 2026-10-25: check them on docs.n8n.io before a
relaunch records this script. The watcher's file, silent-failure-watch.json,
is a second download (Episode.downloads); it needs the n8n API credential and
a Slack one picked after import, and embeds neither.
"""
from __future__ import annotations

import json

from ..tutorial_series import (
    N8N_PLANNED,
    Episode,
    Scene,
    lab_scene,
    payoff_scene,
    scripted,
)

LAB_JSON = """{
  "name": "ATLAS Academy lab: when a step fails",
  "nodes": [
    { "name": "Start", "type": "n8n-nodes-base.manualTrigger",
      "typeVersion": 1, "position": [0, 0], "parameters": {} },
    { "name": "HTTP Request", "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2, "position": [240, 0],
      "parameters": { "method": "GET",
        "url": "https://geocoding-api.open-meteo.com/v1/serch",
        "sendQuery": true, "queryParameters": { "parameters": [
          { "name": "name", "value": "Lisbon" } ] } } },
    { "name": "Edit Fields", "type": "n8n-nodes-base.set",
      "typeVersion": 3.4, "position": [480, 0],
      "parameters": { "assignments": { "assignments": [
          { "id": "lab4-text", "name": "text",
            "value": "={{ $json.results[0].name }} is in {{ $json.results[0].country }}",
            "type": "string" } ] },
        "options": {} } }
  ],
  "connections": {
    "Start": { "main": [[{ "node": "HTTP Request", "type": "main", "index": 0 }]] },
    "HTTP Request": { "main": [[{ "node": "Edit Fields", "type": "main", "index": 0 }]] }
  }
}"""

LAB_FILE = "n8n-when-a-step-fails.json"

# The silent-failure watch: a workflow that never starts fails nothing, so no
# error workflow hears about it. Built as a dict so the Code node's lines are
# escaped by json.dumps, never by hand.
WATCH_FILE = "silent-failure-watch.json"
WATCH_ID_PLACEHOLDER = "PASTE-THE-WORKFLOW-ID-HERE"
WATCH_CODE = """\
// Compares the watched workflow's last start with the age you allow.
// The state lives in the workflow's static data, which n8n saves on
// automatic runs only (not when you click Execute workflow).
const settings = $('Settings').first().json;
const runs = $input.first().json.data || [];
const last = runs[0];
const state = $getWorkflowStaticData('global');

// startedAt, not stoppedAt: a run that failed or is still going did start.
const started = last ? Date.parse(last.startedAt) : NaN;
const ageMinutes = Number.isNaN(started)
  ? null
  : Math.round((Date.now() - started) / 60000);
const overdue = ageMinutes === null || ageMinutes > settings.maxAgeMinutes;

let send = 'none';
let message = '';
if (overdue && !state.alerted) {
  state.alerted = true;
  send = 'overdue';
  message = ageMinutes === null
    ? `Workflow ${settings.workflowId} has no runs on record. Is it on?`
    : `Workflow ${settings.workflowId} last started ${ageMinutes} min ago `
      + `(limit ${settings.maxAgeMinutes}). Is its trigger still on?`;
} else if (!overdue && state.alerted) {
  state.alerted = false;
  send = 'back';
  message = `Workflow ${settings.workflowId} is running again `
    + `(last start ${ageMinutes} min ago).`;
}

return [{ json: { send, message, overdue, ageMinutes,
  workflowId: settings.workflowId,
  lastStartedAt: last ? last.startedAt : null } }];
"""


def _sticky(name: str, content: str, at: tuple[int, int], width: int = 300,
            height: int = 260) -> dict:
    return {"name": name, "type": "n8n-nodes-base.stickyNote", "typeVersion": 1,
            "position": list(at),
            "parameters": {"content": content, "width": width, "height": height}}


WATCH: dict = {
    "name": "ATLAS Academy lab: silent failure watch",
    "nodes": [
        {"name": "Every 15 minutes", "type": "n8n-nodes-base.scheduleTrigger",
         "typeVersion": 1.2, "position": [0, 0],
         "parameters": {"rule": {"interval": [
             {"field": "minutes", "minutesInterval": 15}]}}},
        {"name": "Settings", "type": "n8n-nodes-base.set", "typeVersion": 3.4,
         "position": [220, 0],
         "parameters": {"assignments": {"assignments": [
             {"id": "watch-workflow-id", "name": "workflowId",
              "value": WATCH_ID_PLACEHOLDER, "type": "string"},
             {"id": "watch-max-age", "name": "maxAgeMinutes", "value": 90,
              "type": "number"},
             {"id": "watch-base-url", "name": "baseUrl",
              "value": "https://your-n8n.example.com", "type": "string"}]},
             "options": {}}},
        {"name": "Last run", "type": "n8n-nodes-base.httpRequest",
         "typeVersion": 4.2, "position": [440, 0],
         "parameters": {
             "method": "GET",
             "url": "={{ $json.baseUrl }}/api/v1/executions",
             "authentication": "predefinedCredentialType",
             "nodeCredentialType": "n8nApi",
             "sendQuery": True,
             "queryParameters": {"parameters": [
                 {"name": "workflowId", "value": "={{ $json.workflowId }}"},
                 {"name": "limit", "value": "1"}]},
             "options": {}}},
        {"name": "Overdue?", "type": "n8n-nodes-base.code", "typeVersion": 2,
         "position": [660, 0], "parameters": {"jsCode": WATCH_CODE}},
        {"name": "Anything to say?", "type": "n8n-nodes-base.if",
         "typeVersion": 2, "position": [880, 0],
         "parameters": {"conditions": {
             "options": {"caseSensitive": True, "leftValue": "",
                         "typeValidation": "strict"},
             "conditions": [{"id": "watch-send", "leftValue": "={{ $json.send }}",
                             "rightValue": "none",
                             "operator": {"type": "string",
                                          "operation": "notEquals"}}],
             "combinator": "and"}, "options": {}}},
        {"name": "Tell me in Slack", "type": "n8n-nodes-base.slack",
         "typeVersion": 2.2, "position": [1100, -100],
         "parameters": {"select": "channel",
                        "channelId": {"__rl": True, "value": "#alerts",
                                      "mode": "name"},
                        "text": "={{ $json.message }}", "otherOptions": {}}},
        {"name": "Stay quiet", "type": "n8n-nodes-base.noOp", "typeVersion": 1,
         "position": [1100, 100], "parameters": {}},
        _sticky("Note: what this catches",
                ("## A workflow that never starts\n"
                 "Retry On Fail, On Error and an error workflow only act when a run "
                 "fails. A stopped trigger, or a workflow turned off, fails nothing, "
                 "so they stay silent. This watcher checks the last run instead."),
                (-40, -320), width=420, height=240),
        _sticky("Note: 1. set it up",
                ("## 1. Fill in Settings\n"
                 f"- **workflowId**: replace {WATCH_ID_PLACEHOLDER} with the id from "
                 "the watched workflow's address (…/workflow/<id>)\n"
                 "- **maxAgeMinutes**: the longest gap that is still normal\n"
                 "- **baseUrl**: your n8n address, no slash at the end"),
                (180, 160), width=340, height=260),
        _sticky("Note: 2. the n8n API",
                ("## 2. Ask when it last ran\n"
                 "Create an n8n API key (Settings > n8n API), save it as an n8n API "
                 "credential and pick it in this node. It reads one execution: the "
                 "newest. Keep saving production executions on the watched workflow, "
                 "or there is nothing to read."),
                (400, 160), width=320, height=260),
        _sticky("Note: 3. one alert",
                ("## 3. One alert, then quiet\n"
                 "The Code node remembers whether it already alerted (workflow static "
                 "data). Overdue: one alert. Still overdue: nothing. Running again: "
                 "one 'it's back'. Static data is kept on automatic runs only, so "
                 "publish this watcher to test it."),
                (640, 160), width=320, height=280),
        _sticky("Note: 4. tell me",
                ("## 4. Tell me\n"
                 "Pick your Slack credential and channel. Then give this watcher an "
                 "error workflow too, so you hear if the watcher itself breaks."),
                (1060, 160), width=300, height=220),
    ],
    "connections": {
        "Every 15 minutes": {"main": [[{"node": "Settings", "type": "main",
                                        "index": 0}]]},
        "Settings": {"main": [[{"node": "Last run", "type": "main", "index": 0}]]},
        "Last run": {"main": [[{"node": "Overdue?", "type": "main", "index": 0}]]},
        "Overdue?": {"main": [[{"node": "Anything to say?", "type": "main",
                                "index": 0}]]},
        "Anything to say?": {"main": [
            [{"node": "Tell me in Slack", "type": "main", "index": 0}],
            [{"node": "Stay quiet", "type": "main", "index": 0}]]},
    },
    "settings": {"executionOrder": "v1"},
}
WATCH_JSON = json.dumps(WATCH, indent=2, ensure_ascii=False)

LAB_FILES: dict[str, str] = {LAB_FILE: LAB_JSON + "\n", WATCH_FILE: WATCH_JSON + "\n"}

PICTURE = (
    "A step fails\nthe API is down",
    "It retries\nor takes the error path",
    "You're told\nin Slack, right away",
)

EPISODE: Episode = scripted(
    next(e for e in N8N_PLANNED if e.number == 4),
    title_hook="n8n Error Handling",
    links=(("Handle errors gracefully",
            "https://docs.n8n.io/build/flow-logic/handle-errors-gracefully/"),
           ("Error Trigger node",
            "https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.errortrigger/"),
           ("Node settings",
            ("https://docs.n8n.io/build/understand-workflows/workflow-components/"
             "work-with-nodes/"))),
    ask="What's the most annoying way a workflow has failed on you? Tell me below.",
    topic_said=("Today: what happens when a step in n8n fails, and three ways to "
                "handle it."),
    topic_shown="Retry, error output, error workflow",
    payoff=payoff_scene(
        ("You'll finish with workflows that try again, carry on when they can, and "
         "send you a message the moment something breaks."),
        "Slack: 'Invoice sync failed at HTTP Request'",
        items=PICTURE,
        caption="you hear about it first"),
    hook=Scene("What you'll learn", "hook", "Failures, handled", items=PICTURE,
               code=("Retry On Fail → try again\n"
                     "On Error → carry on, or take the error path\n"
                     "Error workflow → tell you"),
               caption="you hear about it first", said=(
                   ("[sighing] Every workflow fails one day. An app is down, or the data "
                    "is odd. The trick is deciding what happens next."),
                   ("All you need is n8n and one workflow you care about. There's a lab "
                    "file at the end with a step that fails on purpose."))),
    needs=("n8n, Cloud or self-hosted", "the free lab files",
           "Slack, only for the alert"),
    downloads=(WATCH_FILE,),
    scenes=(
        Scene("Why workflows fail", "bullets", "Why workflows fail", items=(
            "Bad or missing data",
            "An app or API that's down",
            "A login that expired",
        ), said=(
            ("First, why do they fail? Often it's the data: a field is empty, or not "
             "what the next node expects."),
            ("Sometimes the other side is down. The API doesn't answer, so the node "
             "can't finish."),
            ("And sometimes a login runs out, and the app says no. By default, any "
             "of these stops the whole run, right there."))),
        Scene("Retry On Fail", "steps", "Try again first", items=(
            "Open the node → Settings",
            "Turn on Retry On Fail",
            "Good for a busy or flaky API",
        ), said=(
            "The gentlest fix is a retry. Open the node, and go to its settings.",
            ("Turn on Retry On Fail. When the node fails, n8n runs it again, until it "
             "works."),
            ("That's perfect for an app that's busy for a moment. It won't fix bad "
             "data, though. Wrong data stays wrong, however often you try."))),
        Scene("On Error", "bullets", "When retrying isn't enough", items=(
            "Stop Workflow: the default, halt here",
            "Continue: carry on with the last good data",
            "Continue (using error output): a second path",
        ), said=(
            ("Next to it is On Error. Stop Workflow is the default: the run halts at "
             "this node."),
            ("Continue carries on to the next node anyway, using the last data that "
             "worked."),
            ("[emphasis] And Continue, using error output, gives the node a second "
             "path. Failures go down it, so you can handle them your way."))),
        Scene("", "quiz", "You want failed items handled separately. Pick…", items=(
            "Stop Workflow", "Continue", "Continue (using error output)",
        ), answer=2, caption="The error output is a path just for failures.", said=(
            ("Quick quiz. You want the items that fail to go somewhere else, like a "
             "list to check by hand. Which setting? Comment your answer."),
            "C. Continue, using error output. Failures get their own path.")),
        Scene("", "subscribe", "Still with me?", said=(
            "If this saves you a bad morning, subscribe.",)),
        Scene("An error workflow", "diagram", "One workflow that tells you", items=(
            "Your workflow\nfails in production",
            "Error Trigger\ngets the details",
            "Slack\nsends you the alert",
        ), said=(
            ("For the failures you didn't plan for, use an error workflow. It's a "
             "separate workflow that starts with the Error Trigger node."),
            ("When a linked workflow fails, the Error Trigger gets the details: which "
             "workflow, and what went wrong."),
            ("Add a Slack node, send a message, and drop those details in. [confident] "
             "Now you hear about it before your customer does."))),
        Scene("Link it up", "steps", "Link the two", items=(
            "Open the workflow that might fail",
            "Options → Settings → Error workflow",
            "Pick your error workflow, then Save",
        ), said=(
            "Now link them. Open the workflow you want to watch.",
            "Go to its settings, and find Error workflow.",
            ("Pick the one you just built, and save. The error workflow itself doesn't "
             "need publishing. And to make a run fail on purpose, say when an order "
             "has no email, add a Stop And Error node with your own message."))),
        Scene("", "pause", "Pause and try this", items=(
            "Build a small error workflow",
            "Link it from one real workflow",
        ), said=(
            ("Pause the video and build a tiny error workflow: an Error Trigger, and "
             "one message to yourself."),
            "Then link it from one real workflow. Resume when it's saved.")),
        Scene("", "quiz", "You click Execute workflow and it fails. Alert?", items=(
            "Yes, straight away", "No: error workflows skip manual runs",
        ), answer=1, caption="The Error Trigger runs on automatic runs only.", said=(
            ("Last quiz. You test by hand, it fails. Does your error workflow send the "
             "alert? Comment."),
            ("B, no. The Error Trigger only runs when an automatic run fails, so you "
             "can't test it by clicking."))),
        Scene("The failure nothing catches", "steps", "When it never starts", items=(
            "Never starts? No error, no alert",
            "A watcher asks the n8n API: last run?",
            "Too long ago → one alert, then quiet",
            "Running again → one 'it's back'",
        ), said=(
            ("One failure slips past all three. If the trigger stops, or the workflow "
             "is off, it never starts. Nothing fails, so nothing tells you."),
            ("So build a watcher: a second workflow, on a schedule, that asks the n8n "
             "API when yours last started."),
            "Too long ago? It sends one alert, then stays quiet.",
            ("When yours runs again, one message: it's back. The watcher is a free "
             "file in the description."))),
        lab_scene("Lab: when a step fails", "Lab: when a step fails", LAB_FILE,
                  do=f"Import {LAB_FILE} · run it · see red",
                  expect="Error output on: the run carries on",
                  stuck="No second path? Pick using error output",
                  challenge="Fix the URL's typo, then add Retry On Fail",
                  said=(
                      ("Your lab. Download the lab file from the description, import it, "
                       "and run it. The HTTP Request goes red, on purpose."),
                      ("Set its On Error to Continue, using error output, and run again. "
                       "Expect the run to carry on, with the error on the second path."),
                      ("Stuck? If no second path appears, you picked plain Continue. "
                       "Choose the one that says using error output."),
                      ("Your challenge: find the typo in the URL, fix it, and turn on "
                       "Retry On Fail."))),
        Scene("Recap", "checklist", "Recap", items=(
            "Retry On Fail: try again first",
            "On Error: stop, continue, or an error path",
            "Error workflow: starts with the Error Trigger",
            "It runs on automatic runs, not test clicks",
        ), said=(
            "Recap. Retry On Fail runs a node again when it fails.",
            "On Error decides the rest: stop, carry on, or send failures down their own path.",
            "An error workflow starts with the Error Trigger, and tells you what broke.",
            ("[happy] And it only fires on automatic runs. One failed step doesn't have "
             "to stop everything any more."))),
    ),
)
