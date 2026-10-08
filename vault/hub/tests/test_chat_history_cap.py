"""What the model is re-sent each turn is bounded; what is stored is not.

Every message used to re-send every earlier message in the thread, so a
conversation's cost grew without limit against a provider budget of 8,000
tokens a minute that the fixed parts already take most of. The largest live
thread was adding ~700 tokens per turn and would only have grown.
"""
from guardian_hub.routers.chat import RECENT_TURNS, _recent_turns


def _thread(n):
    out = []
    for i in range(n):
        out.append({"role": "user", "text": f"q{i}"})
        out.append({"role": "guardian", "text": f"a{i}"})
    return out


def test_a_short_thread_is_sent_whole():
    rows = _thread(3)
    assert _recent_turns(rows) == rows


def test_a_long_thread_is_cut_to_the_recent_tail():
    rows = _thread(40)
    tail = _recent_turns(rows)
    assert len(tail) == RECENT_TURNS
    assert tail[-1] == rows[-1]


def test_the_tail_never_opens_on_an_orphaned_reply():
    # Cutting mid-exchange would hand the model a Vault answer with no
    # question in front of it. The tail starts on a user line.
    rows = [{"role": "guardian", "text": "welcome"}] + _thread(40)
    tail = _recent_turns(rows)
    assert tail[0]["role"] == "user"
    assert len(tail) <= RECENT_TURNS


def test_nothing_in_nothing_out():
    assert _recent_turns(None) == [] and _recent_turns([]) == []
