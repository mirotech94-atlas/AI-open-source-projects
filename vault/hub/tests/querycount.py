"""Count the SQL a request issues, for the "no N+1" guards.

These guards used to assert wall-clock -- `< 2.0s` per request -- and that
made them measure the machine rather than the code. One of them
(`test_assets`) became the suite's only flaky test, failing in one full run
and passing in the next with nothing changed, because a second test suite on
the same laptop was enough to push a request past two seconds. Worse, the
proxy did not even work in the direction it was written for: with a real N+1
injected (one query per device, 20 statements to 55) the old assertion still
passed in 0.195s against its 2.0s budget.

Counting statements measures the thing those tests always meant -- work that
scales with the number of rows -- and gives the same answer on a busy laptop
as on an idle one.

Not for tests of a *time budget* feature, where elapsed time is genuinely the
subject: `test_security`'s `GEO_BUDGET_S` guard is correctly a clock test and
should stay one.
"""

from sqlalchemy import event
from sqlalchemy.engine import Engine


def queries_for(client, path: str) -> int:
    """How many statements one GET puts on the wire. Asserts the 200 itself,
    so a route that starts erroring cannot pass as "very few queries"."""
    n = 0

    def before(conn, cursor, statement, params, context, executemany):
        nonlocal n
        n += 1

    event.listen(Engine, "before_cursor_execute", before)
    try:
        assert client.get(path).status_code == 200, path
    finally:
        event.remove(Engine, "before_cursor_execute", before)
    return n


def statements_for(client, path: str) -> list[str]:
    """Every statement one GET issues, for guards about the shape of the SQL
    rather than its count -- e.g. that a windowed query still carries its
    time cutoff."""
    out: list[str] = []

    def before(conn, cursor, statement, params, context, executemany):
        out.append(statement)

    event.listen(Engine, "before_cursor_execute", before)
    try:
        assert client.get(path).status_code == 200, path
    finally:
        event.remove(Engine, "before_cursor_execute", before)
    return out
