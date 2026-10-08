"""A migration run in-process must not silence unrelated loggers.

`alembic/env.py` calls `logging.config.fileConfig(...)` to set up logging
from `alembic.ini`, and `fileConfig` defaults to `disable_existing_loggers=
True`: every `logging.Logger` object that already exists at that point and
is not named in `alembic.ini`'s own `[loggers]` section gets `.disabled =
True`, permanently, for the life of the process. There is no error and no
warning -- the logger just stops emitting.

That is exactly what `guardian_hub/cli.py`'s `migrate` command does: it runs
`command.upgrade(...)` in the same process as the rest of the CLI, so any
`guardian migrate` that keeps a process alive afterwards (or any test suite
that drives a migration in-process, the way
`test_integrations_task41_fix1.py::
test_migration_0006_moves_a_plaintext_ha_token_into_the_encrypted_blob`
does) silently kills logging for every module already imported at the time
of the upgrade. It surfaced as `test_voice.py::test_the_key_is_never_logged`
failing only in the full suite -- `assert 'invalid_api_key' in ''` -- because
`guardian_hub.voice`'s logger had already been imported and disabled by an
earlier test's in-process migration, so nothing reached `caplog` even though
the code under test logged correctly.

This test reproduces the mechanism directly: create a logger, run a real
Alembic upgrade in-process against a temp SQLite file (mirroring
`test_integrations_task41_fix1.py`'s temp-file handling so no real database
is touched), and assert the logger is still enabled and still emits.
"""
import logging
import pathlib

from alembic import command
from alembic.config import Config

from guardian_hub import crypto
from guardian_hub.config import settings


class _ListHandler(logging.Handler):
    """Collects records without going through the root logger's handlers.

    `fileConfig` unconditionally rebuilds the root logger's own handler list
    from `alembic.ini`'s `[logger_root]` section -- that part is ordinary,
    intended behaviour, independent of `disable_existing_loggers`, and it
    would make a `caplog`-based assertion here flaky for a reason that has
    nothing to do with the bug under test (pytest's own capture handler is
    attached to the root logger for the duration of a test, and a migration
    run mid-test can be reconfigured out from under it). A handler attached
    directly to the probe logger sidesteps that: `Logger.handle` skips
    straight past a disabled logger's own handlers, so this still isolates
    exactly the property in question.
    """

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def test_migrating_in_process_does_not_disable_a_pre_existing_logger(tmp_path, monkeypatch):
    probe = logging.getLogger("guardian_hub.tests.alembic_logging_probe")
    probe.disabled = False
    probe.setLevel(logging.DEBUG)
    collector = _ListHandler()
    probe.addHandler(collector)
    try:
        db_path = tmp_path / "logging_probe.db"
        url = f"sqlite:///{db_path}"
        # Same reason as the task-41 migration test: `env.py` reads
        # `settings.db_url()`, and `settings` is already imported, so the
        # temp file has to be pointed at through the attribute rather than
        # the env var (which is only read when `Settings()` is first
        # constructed).
        monkeypatch.setattr(settings, "database_url", url)
        ini = str(pathlib.Path(crypto.__file__).resolve().parent.parent / "alembic.ini")

        command.upgrade(Config(ini), "head")

        assert probe.disabled is False, (
            "logging.config.fileConfig disabled a pre-existing logger during "
            "an in-process migration -- see env.py's disable_existing_loggers "
            "comment"
        )
        probe.debug("probe still emits after migration")
        assert any(r.getMessage() == "probe still emits after migration" for r in collector.records)
    finally:
        probe.removeHandler(collector)
