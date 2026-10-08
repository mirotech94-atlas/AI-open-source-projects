#!/bin/bash
# The only thing GitLab CD's key may run on this host (authorized_keys:
# command="/opt/atlas/bootstrap/deploy/deploy-gate.sh",restrict). It accepts
# exactly "deploy <sha>" and hands the sha to deploy.sh; anything else is
# refused. The sha is validated again inside deploy.sh.
set -euo pipefail
read -r verb sha extra <<< "${SSH_ORIGINAL_COMMAND:-}"
if [ "$verb" != "deploy" ] || [ -n "${extra:-}" ] || ! [[ "${sha:-}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "refused: only 'deploy <40-char sha>' is allowed"; exit 2
fi
exec /opt/atlas/bootstrap/deploy/deploy.sh "$sha"
