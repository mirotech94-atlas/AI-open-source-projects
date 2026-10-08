"""The overview tells the browser whether a microphone can exist.

It rides on OverviewPayload rather than getting its own endpoint because
`Shell` already fetches this payload and already hands a scalar off it
(`chat_example_ip`) into the chat panel, and hands the whole thing to the
wallboard. Both voice consumers therefore learn the capability for free.

The UI hides every voice control when this is false -- not disables, hides.
`remedy.py`'s human tier is the model: say what is true, or say nothing, but
never offer a button that cannot work.
"""
from guardian_hub.config import settings

from tests.test_auth import login, seed_user


def test_voice_is_false_when_no_key_is_set(client, monkeypatch):
    seed_user()
    login(client)
    monkeypatch.setattr(settings, "elevenlabs_api_key", "", raising=False)
    monkeypatch.setattr(settings, "elevenlabs_voice_id", "", raising=False)
    assert client.get("/api/v1/overview").json()["voice"] is False


def test_voice_is_true_when_both_halves_are_configured(client, monkeypatch):
    seed_user()
    login(client)
    monkeypatch.setattr(settings, "elevenlabs_api_key", "sk-test-key", raising=False)
    monkeypatch.setattr(settings, "elevenlabs_voice_id", "voice-xyz", raising=False)
    assert client.get("/api/v1/overview").json()["voice"] is True


def test_a_key_without_a_voice_reads_as_no_voice(client, monkeypatch):
    seed_user()
    login(client)
    monkeypatch.setattr(settings, "elevenlabs_api_key", "sk-test-key", raising=False)
    monkeypatch.setattr(settings, "elevenlabs_voice_id", "", raising=False)
    assert client.get("/api/v1/overview").json()["voice"] is False
