# The CD path: `deploy-gate.sh` and `deploy.sh`

Reliability plan item RA-H1 (FINDINGS.md §F). Until 2026-09-28 these two
scripts existed only on the ATLAS host, in `/opt/atlas/bootstrap/deploy/`.
The copies here were read from the host with `ssh … cat` on 2026-09-28 and
are byte-identical to what runs (sha256 in `SHA256SUMS`). Nothing on the
host was changed to make them.

**This directory is the reviewed source of truth.** The host runs its own
copy at the server path, and the two are kept equal by hand (below).

## How the CI deploy stage reaches them

```
.gitlab-ci.yml  deploy job (phases-12-14 only, after python + ui pass)
  ssh -i $ATLAS_DEPLOY_KEY root@203.0.113.10 "deploy $CI_COMMIT_SHA"
        |
        v
/root/.ssh/authorized_keys on the host, the CD key's line:
  from="203.0.113.10",command="/opt/atlas/bootstrap/deploy/deploy-gate.sh",restrict ssh-ed25519 <key> gitlab-cd->atlas
        |   the key can run nothing else: sshd replaces whatever the client
        |   asked for with the gate, and puts the request in SSH_ORIGINAL_COMMAND
        v
/opt/atlas/bootstrap/deploy/deploy-gate.sh      (= ops/deploy/deploy-gate.sh)
  accepts exactly "deploy <40 hex>", refuses anything else (exit 2)
        |
        v
/opt/atlas/bootstrap/deploy/deploy.sh <sha>     (= ops/deploy/deploy.sh)
  flock, wait for renders and open executor runs (20 min max),
  git archive from aicode, rsync over /opt/atlas (no --delete),
  ATLAS_DEPLOY_SHA in .env, build, init-db, recreate api,
  healthy within 5 min or roll back to atlas-api:previous
```

The CI job never reads these files from the checkout. It sends one string,
and the forced command on the host decides what runs. That is why they live
at a **server path** and not in the image.

`tests/test_deploy_scripts.py` ties the three together, and CI runs it when
anything under `ops/deploy/` changes:

- the files hash to `SHA256SUMS`, so an edit here without a deliberate
  resync (and a new hash) fails;
- the CI deploy job sends `deploy $CI_COMMIT_SHA` to the host the gate
  lives on;
- the gate accepts exactly that shape (run with a stub in place of
  `deploy.sh`) and refuses every other shape;
- the gate `exec`s `/opt/atlas/bootstrap/deploy/deploy.sh`, the path this
  README and the authorized_keys line name;
- both parse (`bash -n`) and carry no credential.

CI cannot check the host copy itself: the CD key may run only
`deploy <sha>`. The drift check is manual (next section).

## Why `ops/deploy/` and not `bootstrap/deploy/`

`deploy.sh` rsyncs the whole commit over `/opt/atlas`. A copy committed at
`bootstrap/deploy/` would overwrite the live, root-run scripts on the next
CD run. Any push to `phases-12-14` could then rewrite the privileged deploy
path, and the running `deploy.sh` would replace itself mid-deploy. Here, the
rsync only drops an inert copy at `/opt/atlas/ops/deploy/`, which nothing
executes.

## Syncing

Check for drift (read-only, needs an ordinary root SSH login, not the CD key):

```bash
ops/deploy/check-prod-sync.sh            # prints same / DRIFTED per file, exit 1 on drift
```

To change the CD path, all of these, in order. Installing a change on the
host needs Miro's approval, because it is the privileged deploy path.

1. Edit `ops/deploy/*.sh` here, run `bash -n`, and rehearse on the host with
   `ATLAS_DEPLOY_DRY=1 /opt/atlas/bootstrap/deploy/deploy.sh <sha>` (the
   dry run changes nothing).
2. Update `SHA256SUMS` (`shasum -a 256 deploy.sh deploy-gate.sh > SHA256SUMS`
   in this directory) and commit it with the edit.
3. Once the commit has deployed, the reviewed copy is on the host at
   `/opt/atlas/ops/deploy/`. With Miro's OK, install it:
   `install -m 0755 /opt/atlas/ops/deploy/deploy.sh /opt/atlas/ops/deploy/deploy-gate.sh /opt/atlas/bootstrap/deploy/`.
   Do not run this while a deploy holds `/var/lock/atlas-deploy.lock`.
4. Run `ops/deploy/check-prod-sync.sh` and expect `same` for both.

If the host copy was edited in place (drift the other way), read it back
with `ssh root@203.0.113.10 cat /opt/atlas/bootstrap/deploy/<file>`,
review the diff, and commit it here with a new `SHA256SUMS`.

## Secrets

None are in either script (checked 2026-09-28: no token, password or key
material). They name the read-only deploy key by path
(`/root/.ssh/atlas_deploy_read_ed25519`, on the host) and the repo URL. The
CD private key exists only in the protected CI variable `ATLAS_DEPLOY_KEY`.
