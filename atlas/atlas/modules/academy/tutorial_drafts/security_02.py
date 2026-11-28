# HOLD: needs recording per tutorial_series_ai.HOLDS; script avoids claiming a demo
"""AI Security #2: least-privilege AI agents, one key, one sheet.

DRAFT, unapproved: needs Miro's approval before it moves to tutorial_scripts/.

Written 2026-11-24 as a draft. Held in tutorial_series_ai.HOLDS (it needs a
screen recording of a full-scope test key and a scoped one), so nothing here
claims a demo was shown: the picture is bullets, steps, a diagram, a scope
list, two quizzes and a pause. The scoped key is named before the risk is
described, everything is about our own test sheet and test folder, and the
fix is taught by steps and a diagram, never claimed as verified on screen.

Sources read on 2026-11-24:

  genai.owasp.org/llmrisk/llm062025-excessive-agency/
      (OWASP Top 10 for LLM Applications, LLM06 Excessive Agency: damaging
      actions in response to "unexpected, ambiguous or manipulated outputs";
      root causes "excessive functionality", "excessive permissions",
      "excessive autonomy"; prevention: offer agents only the extensions they
      need, limit their permissions on downstream systems to the "minimum
      necessary", require human approval for high-impact actions. The page
      does not use the phrase "least privilege")
  genai.owasp.org/llmrisk/llm01-prompt-injection/
      ("Restrict the model's access privileges to the minimum necessary for
      its intended operations")
  developers.google.com/workspace/sheets/api/scopes
      (spreadsheets: "See, edit, create, and delete all your Google Sheets
      spreadsheets."; spreadsheets.readonly: "See all your Google Sheets
      spreadsheets."; drive: "See, edit, create, and delete all of your Google
      Drive files."; drive.file: "See, edit, create, and delete only the
      specific Google Drive files you use with this app.")

Facts that were not read today carry a TODO-VERIFY comment below.
"""
from __future__ import annotations

from ..tutorial_series import (
    SECURITY_PLANNED,
    Episode,
    Scene,
    payoff_scene,
    scripted,
)

STATUS = "draft/unapproved"
OPENER_TOPIC = "giving an AI agent only the access it needs"

PICTURE = (
    "Full-access key:\neverything",
    "Scoped key:\none sheet, read only",
    "Same task,\nless that can break",
)

SCOPES = """https://www.googleapis.com/auth/spreadsheets
  See, edit, create, and delete all your Google Sheets spreadsheets.
https://www.googleapis.com/auth/spreadsheets.readonly
  See all your Google Sheets spreadsheets.
https://www.googleapis.com/auth/drive
  See, edit, create, and delete all of your Google Drive files."""

EPISODE: Episode = scripted(
    next(e for e in SECURITY_PLANNED if e.number == 2),
    title_hook="API Key Scopes",
    title_benefit="One Key, One Sheet",
    title_formula=2,
    links=(("OWASP LLM06: Excessive Agency",
            "https://genai.owasp.org/llmrisk/llm062025-excessive-agency/"),
           ("Google Sheets API scopes",
            "https://developers.google.com/workspace/sheets/api/scopes")),
    ask="What's the smallest key your agent could work with? Tell me below.",
    topic_said=("Today: least-privilege AI agents, giving an agent one key for "
                "one sheet, and nothing more."),
    topic_shown="One key, one sheet",
    payoff=payoff_scene(
        ("By the end, you'll read a key's scope before you paste it, and hand an "
         "agent only what its job needs."),
        "Full-access key → scoped key, read only, one sheet",
        items=PICTURE,
        caption="the smallest key that does the job"),
    hook=Scene("What you'll learn", "hook", "The smallest key", items=PICTURE,
               code=SCOPES, caption="the smallest key that does the job", said=(
                   ("[curious] Which key do you give your agent, the one that opens "
                    "everything, or the one that opens one sheet?"),
                   ("We'll name the safer choice first, then look at what each key "
                    "lets go wrong, on test data only."),
                   "All you need is an agent, a test sheet, and a test folder.")),
    needs=("an AI agent that uses a key", "a test sheet", "a test folder"),
    scenes=(
        Scene("Least privilege in one line", "bullets", "Least privilege", items=(
            "Give an agent only the access its job needs",
            "Fewer powers means fewer ways to go wrong",
            "Sheet needs reading? Then read-only",
        ), said=(
            ("Least privilege is one idea. Give an agent only the access its job "
             "needs, and no more."),
            ("OWASP calls the opposite excessive agency. Too many tools, too many "
             "permissions, too much freedom to act."),
            ("So if the job is reading one sheet, the key reads one sheet. Not "
             "everything in the account."))),  # TODO-VERIFY: "excessive agency" is LLM06 in the 2025 list; read genai.owasp.org/llm-top-10/
        Scene("Why it matters for agents", "bullets", "Why agents need small keys", items=(
            "An agent acts, it doesn't only answer",
            "A mistake can reach as far as its key",
            "A person approves the big actions",
        ), said=(
            ("An agent doesn't only answer questions. It uses keys to read, write "
             "and delete things for you."),
            ("So when it's wrong, or fooled by a message it read, the damage can "
             "reach as far as its key, and no further."),
            ("Pair a small key with an approval step for anything big. That's two "
             "layers, instead of one."))),
        Scene("The defense first", "steps", "The scoped key, step by step", items=(
            "Make a key just for this agent",
            "Choose read only, not edit",
            "Share only the one sheet it needs",
        ), said=(
            ("Here's the defense, before any risk. Make a separate key for this one "
             "agent. Never reuse your own."),
            "Pick read-only if the agent only reads. Edit rights are a separate choice.",
            ("And share just the one sheet with it. [confident] Now the key can't "
             "see anything else."))),  # TODO-VERIFY: sharing a single sheet with a dedicated account limits a service account's reach; read the Google Sheets/Drive sharing docs
        Scene("", "quiz", "The agent only reads a sheet. Which key?", items=(
            "Edit all sheets", "Read-only, one sheet",
        ), answer=1, caption="Read-only on one sheet is the smallest key for the job.",
              said=(
            "Quick quiz. The agent only reads one sheet. Which key does it get? Comment.",
            "B, read-only on that one sheet. It's the smallest key that still does the job.")),
        Scene("Read the scope first", "code", "Read the scope before you paste",
              code=SCOPES, marks=((0, 2), (2, 4), (4, 6)),
              caption="Google Sheets API scopes", said=(
                  ("Before you paste any key, read its scope. This is the Google "
                   "Sheets list. The first one can see, edit, create and delete "
                   "every spreadsheet."),
                  ("The second only sees spreadsheets. Smaller, and read-only."),
                  ("The Drive scope reaches all your files, far past any sheet. For "
                   "a sheet-reading agent, that's much too wide."))),
        Scene("What a wide key allows", "diagram", "What a wide key allows", items=(
            "Agent gets a confusing request",
            "It picks a delete action",
            "Full key: the test folder goes",
            "Scoped key: the request is refused",
        ), said=(
            ("Now the risk, on our test folder only. Say the agent gets an unclear "
             "or manipulated request."),
            ("It picks a delete action. Models do get things wrong, and nobody has to be "
             "attacking you for that to happen."),
            "With the full key, the delete is allowed, and the test folder is gone.",
            ("[emphasis] With the scoped key, the service refuses. The mistake still "
             "happens, but it has nowhere to land."))),  # TODO-VERIFY: a read-only scope makes the API refuse writes and deletes; read the Sheets/Drive API error docs
        Scene("Test it your way", "steps", "Test the scoped key", items=(
            "Use your test sheet and test folder only",
            "Ask for something outside the scope",
            "Expect a refusal, then read the message",
        ), said=(
            ("You check this yourself, on test data. Use a test sheet and a test "
             "folder, never your real ones."),
            "Then ask the agent for something outside its scope, like deleting the folder.",
            ("A refusal means the key is doing its job. [emphasis] If the action "
             "goes through, the key is too wide. Narrow it and try again."))),
        Scene("", "subscribe", "Still with me?", said=(
            "If this helps, subscribe for the rest of the AI security lessons.",)),
        Scene("", "pause", "Pause and try this", items=(
            "List every key your agent holds",
            "Write the smallest scope each one needs",
        ), said=(
            "Pause the video. List every key your agent holds, including ones in old workflows.",
            "Next to each, write the smallest scope its job needs, and resume.")),
        Scene("Keep keys small", "steps", "Habits that keep keys small", items=(
            "One key per agent, one job each",
            "Read the scope before you paste",
            "Rotate a key you can't account for",
        ), said=(
            ("Three habits. One key per agent, so one mistake touches one job, and you "
             "always know which key to replace."),
            ("Read the scope every time. The name of a key tells you little, but the "
             "scope list tells you exactly what it can do."),
            ("If you can't say where a key went, replace it. [relaxed] A new key "
             "is cheap."))),
        Scene("", "quiz", "A key's name says 'sheets'. Is it read-only?", items=(
            "Yes, the name says so", "Only the scope can tell you",
        ), answer=1, caption="Names don't grant or limit anything. Scopes do.", said=(
            "Last quiz. A key is named sheets. Does that mean it's read-only? Comment.",
            ("B. Only the scope can tell you. A friendly name can sit on a very wide "
             "key."))),
        Scene("Recap", "checklist", "Recap", items=(
            "Give an agent only what its job needs",
            "One key per agent, read-only if it only reads",
            "Share just the one sheet",
            "Read the scope before you paste",
        ), said=(
            "Recap. Give an agent only the access its job needs.",
            "One key per agent, and read-only when it only reads.",
            "Share just the one sheet, and read the scope before you paste.",
            ("[happy] And when a mistake happens, a small key keeps it small. One "
             "key, one sheet."))),
    ),
)
