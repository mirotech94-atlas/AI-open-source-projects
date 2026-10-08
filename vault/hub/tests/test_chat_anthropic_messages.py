"""The conversation `routers/chat.py` builds is a valid Messages API request.

The chat loop keeps ONE message list for every provider, in the shape Groq's
OpenAI-compatible endpoint takes: a leading system prompt, a `system` nudge
appended mid-conversation when the model promised a change instead of making
it, and each tool's output appended as an `assistant` turn. Anthropic accepts
none of that as written, and production showed the result: a customer typed
"trust" at an unverified-devices alert, Claude asked "which of these -- or
both?", the customer said "both", Claude promised to do it, the loop appended
its do-it-now nudge -- and Anthropic returned 400 ("role 'system' must follow
a 'user' message ..."), so the turn fell through to the fixed rule set, which
cannot trust anything. The same list also ended on an assistant turn after
every tool round (a prefill, which Claude Sonnet 5 rejects) and carried an
empty assistant turn whenever a tool call came back with no text.

`_anthropic_messages` translates the shared list at the provider boundary.
These tests pin each rule to the failure it prevents.
"""
from guardian_hub.chat import providers


def _shape(messages):
    return [(m["role"], m["content"]) for m in messages]


def test_the_leading_system_prompt_goes_out_of_band():
    system, convo = providers._anthropic_messages([
        {"role": "system", "content": "You are Vault."},
        {"role": "user", "content": "hi"},
    ])
    assert system == "You are Vault."
    assert _shape(convo) == [("user", "hi")]


def test_the_do_it_nudge_after_a_promise_becomes_a_user_turn():
    """The production failure: [..., assistant promise, system nudge]."""
    system, convo = providers._anthropic_messages([
        {"role": "system", "content": "You are Vault."},
        {"role": "assistant", "content": "Which do you recognise -- or both?"},
        {"role": "user", "content": "both"},
        {"role": "assistant", "content": "I will mark both as trusted."},
        {"role": "system", "content": "You just promised to do something instead of doing it."},
    ])
    assert all(m["role"] != "system" for m in convo)
    assert convo[-1]["role"] == "user"
    assert "You just promised" in convo[-1]["content"]
    assert system == "You are Vault."


def test_tool_output_is_an_observation_the_model_reads_not_a_prefill():
    _, convo = providers._anthropic_messages([
        {"role": "system", "content": "s"},
        {"role": "user", "content": "trust both"},
        {"role": "assistant", "content": ""},
        {"role": "assistant", "content": '<untrusted_tool_output tool="trust_devices">done</untrusted_tool_output>'},
    ])
    # Ends on a user turn (no prefill), keeps the untrusted wrapper intact,
    # and never sends an empty turn.
    assert convo[-1]["role"] == "user"
    assert convo[-1]["content"].count("<untrusted_tool_output") == 1
    assert all(m["content"].strip() for m in convo)


def test_roles_alternate_and_nothing_is_lost():
    _, convo = providers._anthropic_messages([
        {"role": "system", "content": "s"},
        {"role": "user", "content": "a"},
        {"role": "system", "content": "nudge"},
        {"role": "user", "content": "b"},
    ])
    roles = [m["role"] for m in convo]
    assert all(x != y for x, y in zip(roles, roles[1:])), roles
    text = " ".join(m["content"] for m in convo)
    for part in ("a", "nudge", "b"):
        assert part in text


def test_a_conversation_that_ends_on_the_model_gets_a_closing_user_turn():
    _, convo = providers._anthropic_messages([
        {"role": "system", "content": "s"},
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "partial answer"},
    ])
    assert convo[-1]["role"] == "user"


def test_call_anthropic_sends_the_translated_list(monkeypatch):
    sent = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"content": [{"type": "text", "text": "ok"}]}'

    def fake_urlopen(req, timeout=None, context=None):
        import json
        sent.update(json.loads(req.data))
        return _Resp()

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    providers._call_anthropic([
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "both"},
        {"role": "assistant", "content": "I will trust both."},
        {"role": "system", "content": "Do it now."},
    ], tools=[], want_tools=False)
    assert sent["system"] == "sys"
    assert [m["role"] for m in sent["messages"]] == ["user", "assistant", "user"]


# ---------------------------------------------------------------------------
# Groq: stop paying for requests that cannot succeed
# ---------------------------------------------------------------------------

def _http_error(code, body):
    import io
    import urllib.error
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(body.encode()))


def test_a_model_the_account_cannot_use_is_not_retried_every_turn(monkeypatch):
    """`groq/compound` and `groq/compound-mini` answered 404 model_not_found
    on every production turn -- two wasted round trips before the ladder moved
    on, each time."""
    monkeypatch.setattr(providers, "_GROQ_COOLDOWN", {})
    calls = []

    def fake(model, *a, **k):
        calls.append(model)
        raise _http_error(404, '{"error":{"code":"model_not_found"}}')

    monkeypatch.setattr(providers, "_call_groq_model", fake)
    import pytest
    with pytest.raises(Exception):
        providers._call_groq([], [], False, 0)
    first = list(calls)
    calls.clear()
    with pytest.raises(Exception):
        providers._call_groq([], [], False, 0)
    assert first, "nothing was tried"
    assert calls == [], f"retried models that just said they do not exist: {calls}"


def test_a_request_too_large_for_one_model_skips_models_no_larger(monkeypatch):
    """A 413 "request too large ... tokens per minute" is about the REQUEST,
    not the moment: every model with the same or a smaller per-minute budget
    will refuse it too. Production tried all three 8k models in turn."""
    monkeypatch.setattr(providers, "_GROQ_COOLDOWN", {})
    calls = []

    def fake(model, *a, **k):
        calls.append(model)
        raise _http_error(413, '{"error":{"message":"Request too large ... tokens per minute (TPM)"}}')

    monkeypatch.setattr(providers, "_call_groq_model", fake)
    import pytest
    with pytest.raises(Exception):
        providers._call_groq([], [], False, 0)
    tpm = providers.GROQ_MODELS
    first_tpm = tpm[calls[0]]["tpm"]
    assert all(tpm[m]["tpm"] > first_tpm for m in calls[1:]), calls
