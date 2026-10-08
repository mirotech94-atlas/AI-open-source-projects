import pathlib
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
#
# `fileConfig` defaults to `disable_existing_loggers=True`: it permanently
# disables every `logging.Logger` object that already exists at this point
# and is not named in alembic.ini's own `[loggers]` section, with no error
# and no warning. `guardian migrate` (cli.py) runs `command.upgrade(...)` --
# and therefore this file -- in the SAME PROCESS as the rest of the CLI, so
# without this flag a migration silently kills logging for every module
# already imported, for the life of that process. It surfaced as
# `test_voice.py::test_the_key_is_never_logged` failing only in the full
# suite (`assert 'invalid_api_key' in ''`): an earlier test's in-process
# migration had already disabled `guardian_hub.voice`'s logger.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# In-process callers (`cli.py`, `selftest.py`) already have `guardian_hub`
# imported, but `alembic -c hub/alembic.ini ...` run as a SUBPROCESS from the
# repo root does not: `guardian_hub` lives a directory down and is not
# importable unless the package happens to be installed. Both migration tests
# invoke it exactly that way. Put `hub/` on the path here rather than making
# every caller remember a PYTHONPATH -- without it the import below dies with
# ModuleNotFoundError and the failure reads as a broken migration rather than
# a missing path, which is how it read for an hour.
import sys as _sys
_HUB = str(pathlib.Path(__file__).resolve().parents[1])
if _HUB not in _sys.path:
    _sys.path.insert(0, _HUB)

# add your model's MetaData object here
# for 'autogenerate' support
from guardian_hub.db import Base
from guardian_hub import models  # noqa: F401  (registers every table on Base.metadata)
from guardian_hub.config import settings

target_metadata = Base.metadata

config.set_main_option("sqlalchemy.url", settings.db_url())

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
