#!/bin/zsh
# Install or update the ATLAS agent on this Mac, then restart it.
#
#   bootstrap/mac/install-agent.sh
#
# What it does, and nothing else:
#   1. checks the repo copy compiles under the Python launchd runs it with
#      (/usr/bin/python3, 3.9);
#   2. keeps the running copy as ~/.config/atlas/mac_agent.py.bak;
#   3. copies bootstrap/mac/mac_agent.py to ~/.config/atlas/mac_agent.py;
#   4. installs ~/Library/LaunchAgents/com.atlas.mac-agent.plist if it is
#      missing, and loads it;
#   5. restarts the agent: launchctl kickstart -k gui/$UID/com.atlas.mac-agent.
#
# It never reads, writes or prints the token (~/.config/atlas/mac-agent-token).
# An agent older than the server answers "unknown operation" for anything
# added since (mac.new_note, 2026-10-06) and runs nothing; this is the fix.
set -euo pipefail
here=${0:A:h}
label=com.atlas.mac-agent
config="$HOME/.config/atlas"
dest="$config/mac_agent.py"
plist="$HOME/Library/LaunchAgents/$label.plist"
domain="gui/$(id -u)"

/usr/bin/python3 - "$here/mac_agent.py" <<'PY'
import ast, sys
ast.parse(open(sys.argv[1]).read(), sys.argv[1])
PY

mkdir -p "$config"
chmod 700 "$config"
if [[ -f "$dest" ]]; then
  cp -p "$dest" "$dest.bak"
fi
install -m 0644 "$here/mac_agent.py" "$dest"
echo "installed $dest"

if [[ ! -f "$plist" ]]; then
  mkdir -p "${plist:h}"
  sed "s#__HOME__#$HOME#g" "$here/$label.plist" > "$plist"
  launchctl bootstrap "$domain" "$plist"
  echo "loaded $plist"
fi

launchctl kickstart -k "$domain/$label"
echo "restarted $label; its log: ~/Library/Logs/atlas-mac-agent.log"
if [[ ! -s "$config/mac-agent-token" ]]; then
  echo "note: $config/mac-agent-token is missing; the agent cannot reach ATLAS" >&2
fi
