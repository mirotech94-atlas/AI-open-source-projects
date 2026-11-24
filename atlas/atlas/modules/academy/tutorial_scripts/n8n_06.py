"""n8n #6: webhooks in, API calls out, so almost any app can join a workflow.

Study note: docs/academy-references/n8n_06.md

Written 2026-10-18 by the script writer (docs/academy-script-writer.md). No
live demo: no new recording can be made, so the picture is bullets, steps,
one expression on screen, two quizzes and a lab.

Every product fact was read on the official n8n docs on 2026-10-18:

  docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook.md
      ("receive data from apps and services when an event occurs. It's a
      trigger node"; "two Webhook URLs: test and production"; Production
      "n8n registers a production webhook when you publish the workflow";
      HTTP Method DELETE, GET, HEAD, PATCH, POST, PUT; Path "randomly
      generated" by default, or your own, with route parameters such as
      /:variable; authentication Basic auth, Header auth, JWT auth, None;
      Respond: Immediately "returns the response code and the message
      Workflow got started", When Last Node Finishes "the data output from
      the last node executed", Using 'Respond to Webhook' Node; Response
      Data: First Entry JSON; the request is { body, headers, params,
      query } in $json)
  .../n8n-nodes-base.webhook/workflow-development.md
      ("Select Listen for test event to register the webhook before
      sending the data to the test webhook. The test webhook stays active
      for 120 seconds"; production: "ensure that you have saved and
      published the workflow")
  .../n8n-nodes-base.webhook/common-issues.md
      (Production URL "Until workflow is unpublished", data not shown in
      the editor; "n8n only permits registering one webhook for each path
      and HTTP method combination")
  docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.httprequest.md
      ("make HTTP requests to query data from any app or service with a
      REST API"; Method, URL; "n8n recommends using the Predefined
      Credential Type option when it's available"; Generic credentials
      include Header auth and Query auth; Send Query Parameters "act as
      filters"; Send Headers "metadata or context about your request"; Send
      Body, Body Content Type JSON; Import cURL: "From the HTTP Request
      node's Parameters tab, select Import cURL", paste, Import, "This
      overwrites any existing configuration"; it "always imports any
      parameter values as strings")
  docs.n8n.io/integrations/builtin/credentials/httprequest.md
      (Header auth: the header Name and its Value; Query auth: "a single
      key/value query parameter")

The lab's Webhook parameters (httpMethod, path, responseMode "lastNode",
responseData "firstEntryJson") were checked on 2026-10-18 against n8n's
own source, raw.githubusercontent.com/n8n-io/n8n/master/packages/
nodes-base/nodes/Webhook/description.ts. Its path, city-lookup, is new, so
it can't collide with n8n #1's city-info. Open-Meteo's
geocoding-api.open-meteo.com/v1/search?name=Lisbon answered 200 the same
day with results[0].name "Lisbon" and country "Portugal" (curl). The lab
uses only the Webhook, HTTP Request and Edit Fields (Set v3.4), no
credentials.
"""
from __future__ import annotations

from ..tutorial_series import (
    N8N_PLANNED,
    Episode,
    Scene,
    lab_scene,
    payoff_scene,
    scripted,
)

LAB_JSON = """{
  "name": "ATLAS Academy lab: your own city API",
  "nodes": [
    { "name": "Webhook", "type": "n8n-nodes-base.webhook",
      "typeVersion": 2, "position": [0, 0],
      "parameters": { "httpMethod": "GET", "path": "city-lookup",
        "responseMode": "lastNode", "responseData": "firstEntryJson",
        "options": {} } },
    { "name": "HTTP Request", "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2, "position": [240, 0],
      "parameters": { "method": "GET",
        "url": "https://geocoding-api.open-meteo.com/v1/search",
        "sendQuery": true, "queryParameters": { "parameters": [
          { "name": "name", "value": "={{ $json.query.city }}" } ] } } },
    { "name": "Edit Fields", "type": "n8n-nodes-base.set",
      "typeVersion": 3.4, "position": [480, 0],
      "parameters": { "assignments": { "assignments": [
          { "id": "lab6-answer", "name": "answer",
            "value": "={{ $json.results[0].name }} is in {{ $json.results[0].country }}",
            "type": "string" } ] },
        "options": {} } }
  ],
  "connections": {
    "Webhook": { "main": [[{ "node": "HTTP Request", "type": "main", "index": 0 }]] },
    "HTTP Request": { "main": [[{ "node": "Edit Fields", "type": "main", "index": 0 }]] }
  }
}"""

LAB_FILE = "n8n-your-own-api.json"
LAB_FILES: dict[str, str] = {LAB_FILE: LAB_JSON + "\n"}

EXPRESSION = """{{ $json.query.city }}          # what the caller sent: ?city=Lisbon
{{ $json.results[0].name }}     # what the API answered: Lisbon"""

PICTURE = (
    "An app calls you\nthe Webhook",
    "You call an API\nHTTP Request",
    "The answer goes back\nto the caller",
)

EPISODE: Episode = scripted(
    next(e for e in N8N_PLANNED if e.number == 6),
    title_hook="n8n Webhooks and APIs",
    links=(("Webhook node",
            "https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/"),
           ("HTTP Request node",
            ("https://docs.n8n.io/integrations/builtin/core-nodes/"
             "n8n-nodes-base.httprequest/")),
           ("HTTP Request credentials",
            "https://docs.n8n.io/integrations/builtin/credentials/httprequest/")),
    ask="Which app do you wish you could connect to n8n? Tell me below.",
    topic_said=("Today: webhooks to let apps call n8n, and HTTP Request to call any "
                "app's API."),
    topic_shown="Webhooks in, APIs out",
    payoff=payoff_scene(
        ("You'll finish with a little web address of your own: send it a city, and it "
         "answers with the country."),
        "GET …/city-lookup?city=Lisbon → { \"answer\": \"Lisbon is in Portugal\" }",
        items=PICTURE,
        caption="your workflow, as an API"),
    hook=Scene("What you'll learn", "hook", "Any app with an API", items=PICTURE,
               code=EXPRESSION, caption="your workflow, as an API", said=(
                   ("No ready-made node for the app you use? [curious] Doesn't matter, "
                    "if it has an API."),
                   ("All you need is n8n. The lab file at the end calls a free city "
                    "search, so you don't even need an account."))),
    needs=("n8n, Cloud or self-hosted", "the free lab file", "a web browser"),
    scenes=(
        Scene("In and out", "bullets", "Two directions", items=(
            "Webhook: an app calls n8n",
            "HTTP Request: n8n calls an app",
            "Together: almost any app joins in",
        ), said=(
            ("Two directions. A Webhook is a trigger: another app calls its web "
             "address, and your workflow starts."),
            ("HTTP Request goes the other way. n8n calls an app's API, to fetch "
             "data or send it."),
            ("Put them together, and anything with a REST API can join a workflow, "
             "even without its own node."))),
        Scene("The Webhook", "steps", "Set up the Webhook", items=(
            "HTTP Method: GET, POST and others",
            "Path: your own, like city-lookup",
            "Test URL while building · Production URL once published",
        ), said=(
            ("Add a Webhook node and pick the method. GET to ask for something, POST "
             "to send data."),
            ("The path is random to start with. Give it a readable one, like city "
             "lookup. Each path and method pair can only be used once."),
            ("[emphasis] There are two addresses. The test one, while you build, and "
             "the production one, which only works once you publish. Its runs "
             "show up under Executions, not on the canvas."))),
        Scene("Answer the caller", "bullets", "Answer the caller", items=(
            "Immediately: 'Workflow got started'",
            "When Last Node Finishes: the result",
            "Respond to Webhook node: you decide",
        ), said=(
            ("Next, Respond. Immediately sends back a short note that the workflow "
             "started, and that's all."),
            "When Last Node Finishes sends back what your last node produced.",
            "Or add a Respond to Webhook node, and shape the answer yourself.")),
        Scene("", "quiz", "The caller should get your result. Respond…", items=(
            "Immediately", "When Last Node Finishes", "Never",
        ), answer=1, caption="When Last Node Finishes sends the last node's data.", said=(
            ("Quick quiz. The app calling you wants the result, not just a thank you. "
             "Which Respond option? Comment."),
            "B. When Last Node Finishes. Immediately only says it started.")),
        Scene("HTTP Request", "steps", "Call any API", items=(
            "Method and URL, from the API's docs",
            "Query parameters: filters",
            "Headers: extra details",
            "Body: JSON, when you send data",
        ), said=(
            ("Now the other way. In an HTTP Request node, set the method and the URL "
             "from the API's docs."),
            ("Query parameters are filters, like name equals Lisbon."),
            "Headers carry extra details about the request.",
            ("And when you send data, turn on Send Body and choose JSON."))),
        Scene("", "subscribe", "Finding this useful?", said=(
            "If this opens up a few apps for you, subscribe.",)),
        Scene("Signing in", "bullets", "Signing in to an API", items=(
            "Predefined credential: use it when it exists",
            "Header auth: a header name and value",
            "Query auth: one key in the query",
        ), said=(
            ("Most APIs want to know who you are. If n8n already knows the app, pick "
             "Predefined Credential Type. n8n recommends it."),
            ("If not, use a generic one. Header auth sends a header name and its "
             "value."),
            ("Query auth adds one key to the address. Either way, you save it once, "
             "as a credential."))),
        Scene("Import cURL", "steps", "The shortcut: Import cURL", items=(
            "Copy a curl example from the API's docs",
            "HTTP Request → Import cURL → paste",
            "It overwrites the node's settings",
        ), said=(
            ("[excited] Here's the shortcut. Most API docs show curl examples. Copy "
             "one."),
            ("In the HTTP Request node, choose Import cURL, paste it, and import. "
             "Method, headers, body, all filled in."),
            ("Two things to know. It replaces what was in the node, and numbers come "
             "in as text, so check them."))),
        Scene("", "pause", "Pause and try this", items=(
            "Find a curl example in any API's docs",
            "Import it into an HTTP Request node",
        ), said=(
            "Pause the video and find a curl example in the docs of an app you use.",
            "Import it into an HTTP Request node, look at what it filled in, and resume.")),
        Scene("", "quiz", "You call the Test URL and nothing happens. Why?", items=(
            "n8n is down", "Test URLs need Listen for test event", "Wrong city",
        ), answer=1, caption="A test URL listens for 120 seconds after you click.", said=(
            ("Last quiz. You call the test address, and nothing happens. Most likely? "
             "Comment."),
            ("B. Click Listen for test event first. The test address only listens "
             "for two minutes."))),
        lab_scene("Lab: your own API", "Lab: your own API", LAB_FILE,
                  do=f"Import {LAB_FILE} · Listen · open …?city=Lisbon",
                  expect="Your browser shows: Lisbon is in Portugal",
                  stuck="Nothing back? Listen first, then call within 2 min",
                  challenge="Add Header auth to the Webhook",
                  said=(
                      ("Your lab. Download the lab file from the description and "
                       "import it. Open the Webhook, click Listen for test event, then "
                       "open its test address with question mark city equals Lisbon."),
                      ("Expect your browser to show: Lisbon is in Portugal. Your "
                       "workflow just answered like an API."),
                      ("Stuck? If nothing comes back, click Listen again, and call it "
                       "within two minutes."),
                      ("Your challenge: make the Webhook ask for Header auth, so only "
                       "callers with the right header get in."))),
        Scene("Recap", "checklist", "Recap", items=(
            "Webhook: apps call your workflow",
            "Respond: when the last node finishes",
            "HTTP Request: method, URL, query, headers, body",
            "Import cURL from the API's docs",
        ), said=(
            "Recap. A Webhook lets other apps start your workflow.",
            "Respond when the last node finishes, and the caller gets your result.",
            "HTTP Request calls any API: method, address, query, headers and body.",
            ("[happy] And Import cURL fills it in from the docs. Any app with an API "
             "can join your workflows now."))),
    ),
)
