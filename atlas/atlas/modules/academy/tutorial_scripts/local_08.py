"""Local #8: Hermes Agent as a Telegram or Slack bot (the messaging gateway).

Study note: docs/academy-references/local_08.md

Approved by Miro 2026-11-24 (he authorised the review); facts re-read that day.

Written 2026-11-24. No live demo: the picture is the .env lines on screen
(placeholders only, never a real token), steps, bullets, two quizzes and a pause.

Read on 2026-11-24 (New York time):

  hermes-agent.nousresearch.com/docs/user-guide/messaging
      (gateway commands: hermes gateway setup "Interactive setup for all
      messaging platforms", hermes gateway "Run in foreground", hermes
      gateway install "Install as user service", start, stop, status;
      TELEGRAM_ALLOWED_USERS; GATEWAY_ALLOWED_USERS fallback;
      GATEWAY_ALLOW_ALL_USERS=true not recommended for bots with terminal
      access; pairing: unknown users get a one-time code by DM, codes expire
      after 1 hour, hermes pairing approve / list / revoke)
  hermes-agent.nousresearch.com/docs/user-guide/messaging/telegram
      (BotFather /newbot; token goes in ~/.hermes/.env as TELEGRAM_BOT_TOKEN;
      TELEGRAM_ALLOWED_USERS with your numeric ID, commas for several;
      @userinfobot shows the ID; hermes gateway starts the bot)
  hermes-agent.nousresearch.com/docs/user-guide/messaging/slack
      (Socket Mode, no public URL; App-Level Token with connections:write;
      SLACK_BOT_TOKEN starts xoxb-, SLACK_APP_TOKEN starts xapp-, both in
      ~/.hermes/.env; SLACK_ALLOWED_USERS takes Member IDs, "if unset, the
      gateway denies all messages"; reinstall the app after changing scopes)
"""
from __future__ import annotations

from ..tutorial_series import (
    LOCAL_PLANNED,
    Episode,
    Scene,
    payoff_scene,
    scripted,
)

ENV = """# ~/.hermes/.env  (placeholders: never show or share real values)
TELEGRAM_BOT_TOKEN=<token from BotFather>
TELEGRAM_ALLOWED_USERS=<your numeric user ID>"""

PICTURE = (
    "BotFather\ngives a token",
    "Token in\n~/.hermes/.env",
    "Allowed users\nonly",
)

EPISODE: Episode = scripted(
    next(e for e in LOCAL_PLANNED if e.number == 8),
    title="Run Hermes Agent as a Telegram or Slack Bot",
    title_hook="Hermes Telegram Bot",
    title_benefit="Chat Anywhere",
    result="Hermes in chats",
    tags=("hermes agent telegram bot", "hermes gateway", "hermes slack bot",
          "advanced", "ATLAS Academy"),
    links=(("Hermes Agent: messaging",
            "https://hermes-agent.nousresearch.com/docs/user-guide/messaging"),
           ("Hermes Agent: Telegram",
            "https://hermes-agent.nousresearch.com/docs/user-guide/messaging/telegram"),
           ("Hermes Agent: Slack",
            "https://hermes-agent.nousresearch.com/docs/user-guide/messaging/slack")),
    ask="Telegram or Slack: which one will you try first? Tell me below.",
    topic_said=("Today: Hermes Agent's messaging gateway, a Telegram bot with its token "
                "kept secret, a Slack app, and who's allowed to talk to it."),
    topic_shown="Hermes as a chat bot",
    payoff=payoff_scene(
        ("By the end, you can message your own agent from your phone, and only the "
         "people you named can answer back."),
        "hermes gateway · one token, one allowed user",
        items=PICTURE,
        caption="your agent, in your chats"),
    hook=Scene("What you'll learn", "hook", "Hermes in your chats", items=PICTURE,
               code=ENV, caption="your agent, in your chats", said=(
                   ("Your agent lives on one machine. [curious] But you're not always "
                    "sitting at it."),
                   ("All you need is Hermes Agent, plus a Telegram or Slack account. "
                    "The gateway is already part of Hermes."))),
    needs=("Hermes Agent, installed", "A Telegram or Slack account"),
    scenes=(
        Scene("The gateway", "bullets", "What the gateway does", items=(
            "One process connects chat apps to Hermes",
            "Telegram, Slack and more",
            "hermes gateway runs it",
        ), said=(
            ("The messaging gateway connects your chat apps to the agent. You message "
             "the bot, and Hermes answers."),
            ("It works with Telegram and Slack, and with other platforms too. Each "
             "one has its own setup page."),
            ("You start it with one command, hermes gateway. Or run hermes gateway "
             "setup, and a wizard asks the questions for you."))),
        Scene("Telegram", "steps", "Make the Telegram bot", items=(
            "Message @BotFather, send /newbot",
            "Put the token in ~/.hermes/.env",
            "Run hermes gateway",
        ), said=(
            ("In Telegram, message BotFather and send slash newbot. Pick a name, "
             "and a username that ends in bot. It gives you a token."),
            ("That token goes in a file in your dot hermes folder, called dot env, "
             "as TELEGRAM BOT TOKEN."),
            ("Then run hermes gateway. [excited] The bot comes online, and you can "
             "message it."))),
        Scene("Keep the token secret", "code", "Token and allowed user", code=ENV,
              marks=((0, 2), (2, 3)),
              caption="placeholders only", said=(
                  ("That token is a password for your bot. Anyone who has it can "
                   "act as the bot. Keep it in the dot env file, and nowhere else."),
                  ("The next line says who may talk to it. It takes your numeric user "
                   "ID, not your username. A bot called userinfobot will tell you "
                   "yours."))),
        Scene("", "quiz", "Where does the bot token go?", items=(
            "In a public post", "In ~/.hermes/.env",
        ), answer=1, caption="The token is a password. Keep it in .env.", said=(
            ("Quick quiz. Where should your bot token live? Comment your answer."),
            "B. In the dot env file. Never post it, or paste it in a video.")),
        Scene("Slack", "steps", "Or make a Slack app", items=(
            "Create the app, turn on Socket Mode",
            "Two tokens: xoxb- and xapp-",
            "Add the scopes, then reinstall",
        ), said=(
            ("Slack works through Socket Mode. That means no public web address. "
             "You turn it on, and make an app-level token."),
            ("Then there are two tokens in the dot env file. SLACK BOT TOKEN starts "
             "with x o x b, and SLACK APP TOKEN starts with x a p p."),
            ("Add the scopes the page lists, then reinstall the app. Without the "
             "reinstall, new scopes won't apply."))),
        Scene("Run it all day", "steps", "Keep it running", items=(
            "hermes gateway install",
            "hermes gateway start, stop, status",
            "Foreground is fine for a first try",
        ), said=(
            ("Running in the foreground stops when you close the terminal. For a bot "
             "that's always there, run hermes gateway install."),
            ("It sets up a user service, and then start, stop and status control "
             "it. On a Mac, that's a launchd service."),
            ("For your first test, the foreground is fine. You can watch what the "
             "bot does, and stop it with one key."))),
        Scene("", "subscribe", "Running AI at home?", said=(
            "If you're building your own local agent, subscribe for the rest.",)),
        Scene("Who may talk to it", "bullets", "Control who can talk", items=(
            "TELEGRAM_ALLOWED_USERS, SLACK_ALLOWED_USERS",
            "Slack: unset means everyone is denied",
            "Pairing codes for new people",
        ), said=(
            ("Each platform has an allowed users line. Only the IDs you list can use "
             "the bot. Slack takes member IDs, not names."),
            ("And if the Slack line is empty, the gateway denies everyone. That's "
             "the safe default."),
            ("You can also use pairing. A stranger gets a one-time code, and you "
             "approve or revoke them with hermes pairing."))),
        Scene("Don't open it up", "steps", "Never allow everyone", items=(
            "GATEWAY_ALLOW_ALL_USERS is a trap",
            "Your agent may have real tools",
            "Allow named people only",
        ), said=(
            ("There's a switch that allows all users. The docs don't recommend it "
             "for bots with terminal access."),
            ("Think about what your agent can do. If it has tools, anyone who can "
             "message it can use them."),
            ("So list people by name. [emphasis] A short list is a safe list."))),
        Scene("", "pause", "Pause and try this", items=(
            "Make your bot, or your Slack app",
            "Run hermes gateway and say hi",
        ), said=(
            ("Pause the video. Make a Telegram bot, or a Slack app, and put the "
             "token in your dot env file. Add only your own ID."),
            ("Run hermes gateway, and send it a message. Resume when it answers."))),
        Scene("", "quiz", "Slack's allowed users line is empty. What happens?", items=(
            "Everyone is allowed", "Everyone is denied",
        ), answer=1, caption="If unset, the gateway denies all messages.", said=(
            ("Last quiz. The Slack allowed users line is empty. Who can talk to "
             "the bot? Comment."),
            "B. Nobody. Empty means deny all.")),
        Scene("Recap", "checklist", "Recap", items=(
            "hermes gateway connects chat apps to Hermes",
            "Tokens live in ~/.hermes/.env only",
            "Allowed users: name them, don't open it",
            "Pairing codes for anyone new",
        ), said=(
            "Recap. The gateway connects your chat apps to your agent.",
            "Tokens are passwords, so they live in the dot env file only.",
            "Name the people who may talk to it. Never allow everyone.",
            ("[happy] And pairing codes let you add new people on purpose."))),
    ),
)
