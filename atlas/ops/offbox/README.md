# ops/offbox: copies to the second box (host jobs, not ATLAS, not installed)

Two Mac host scripts. ATLAS only reads the status file each one writes.
Both need `ATLAS_OFFBOX_TARGET=user@host:/dir/` (the second box; Miro names it)
and the Mac's own ssh key. Both refuse (exit 2) when the target is unset or has
no colon (rsync would then copy locally and report success).

| Script | Sends | Status file (in `data/app/backups/`) |
|---|---|---|
| `copy_dump.sh` | the newest `atlas-*.dump.age` (encrypted; the plain dump never leaves the Mac) | `offbox.json` `{at, ok, file, bytes, sha256, target_host}` |
| `copy_branch_tip.sh` | a `git bundle` of all branches and tags, verified first | `offbox_branches.json` `{at, ok, file, bytes}` |

- `ok` is true only after the remote `sha256` equals the local one.
- No `--delete`: nothing is removed on either side. Retention on the second box
  is Miro's to set.
- The `.age` file can only be read with the key on the Mac. A second-box drill
  therefore needs the Mac key (see `docs/restore-drill.md`).
- The nightly `atlas-backup-local.sh` already rsyncs to `ATLAS_BACKUP_OFFSITE`;
  `copy_dump.sh` is a second copy to a different box, run after it.

## Install (lead; not done)

A launchd job like the ones in `ops/watchdog/host/`: `ProgramArguments`
`/bin/bash <repo>/ops/offbox/copy_dump.sh`, `EnvironmentVariables`
`ATLAS_OFFBOX_TARGET`, `StartCalendarInterval` shortly after the backup (the
backup runs at its own time; pick 30 minutes later). Same for
`copy_branch_tip.sh`. No ATLAS check reads these files yet; that is a follow-up
once the target exists (a missing or `ok:false` file should read UNKNOWN or
FAILING, never OK).
