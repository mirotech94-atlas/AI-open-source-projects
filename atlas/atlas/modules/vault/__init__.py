"""vault — documents indexed for recall, and a reading log that becomes memory.

Watches: the local document vault (`VAULT_DIR`, default `data/vault`) —
leases, insurance policies, contracts, receipts, whatever the owner drops there as
`.txt`, `.md` or `.pdf`. Nothing here fetches from anywhere; it only reads
what is already on disk. Files are hashed, chunked and remembered as
semantic memory in the `"vault"` namespace, so "when does my insurance
renew?" is answered by the concierge through `recall` over that namespace —
there is no separate question-answering endpoint in this module.

May do: read files under `VAULT_DIR` and index new or changed ones into
ATLAS's own memory (`vault.reindex`, Tier AUTO — a filesystem read and a
write to memory, nothing external); log a book, article or video and what
was learned into `library_entries` and the `"library"` memory namespace
(`vault.log_reading`, Tier AUTO, already wired to the `vault_log_reading`
tool).

Endpoints assumed: none. This module never leaves the box — no network
calls, no credentials, no `requires_env`.
"""
from __future__ import annotations

from datetime import timedelta

from ...kernel.contracts import Action, Check, Severity, Tier
from .index import check_index, reindex, reindex_dry, reindex_rollback
from .library import log_dry, log_reading, unlog


class VaultModule:
    name = "vault"
    title = "Vault & Library"
    requires_env: tuple[str, ...] = ()
    optional_env: tuple[str, ...] = ()
    setup_note = ("Drop documents under data/vault (or set VAULT_DIR) and run "
                  "vault.reindex to make them recallable.")

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return (
            Check("index", check_index, timedelta(hours=6), Severity.NORMAL,
                  "documents indexed from the vault directory",
                  baseline_sigma=None),
        )

    def actions(self):
        return (
            Action(
                name="vault.reindex",
                tier=Tier.AUTO,     # reads the filesystem, writes only to memory
                summary="Re-scan the vault directory and index new or changed documents",
                run=reindex, dry_run=reindex_dry, rollback=reindex_rollback,
                params={},
                max_per_hour=6,
            ),
            Action(
                name="vault.log_reading",
                tier=Tier.AUTO,     # a personal note, reversible, nothing external
                summary="Log a book, article or video and what was learned",
                run=log_reading, dry_run=log_dry, rollback=unlog,
                params={"kind": "book | article | video | note",
                        "title": "title of the thing read or watched",
                        "notes": "what was learned or why it matters",
                        "source": "URL or citation, optional"},
                max_per_hour=20,
            ),
        )

    async def signals(self):
        return ()

    def brief(self) -> str:
        return "Vault: documents indexed, library entries logged"

    async def agenda(self, within: timedelta):
        return ()

    async def inbox(self, limit: int = 10):
        return ()


MODULE = VaultModule()
