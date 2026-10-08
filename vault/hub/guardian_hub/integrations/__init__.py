"""Integration providers (Task 40: ntfy/slack/teams/webhook; Task 41 adds
discord/pagerduty/telegram/jira_servicenow/homeassistant on the same
registry). Import `guardian_hub.integrations.registry` — it re-exports the
`base` interfaces (`Integration`, `Message`, `SendContext`, `SendResult`,
`InboundResult`) alongside `REGISTRY`/`register`/`deliver`/`watchdog_job`,
so callers and tests only need that one import.
"""
