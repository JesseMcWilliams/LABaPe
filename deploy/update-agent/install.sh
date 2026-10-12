#!/usr/bin/env bash
# Install (or refresh) the LABaPe update agent for the current user (design §23.2):
#   deploy/update-agent/install.sh [--repo DIR] [--signers FILE]
# Needs lingering for the user (`sudo loginctl enable-linger <user>`, which
# the installer's Update.Agent step does), so the user's systemd keeps
# running without a login.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
SIGNERS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="${2:?}"; shift ;;
    --signers) SIGNERS="${2:?}"; shift ;;
    *) sed -n '2,7p' "$0" >&2; exit 1 ;;
  esac
  shift
done

mkdir -p "$HOME/.local/libexec" "$HOME/.config/systemd/user" "$HOME/.config/labape" "$REPO/container/update"
install -m 0755 "$HERE/labape-update.sh" "$HOME/.local/libexec/labape-update.sh"

# Who may sign releases: the repository's list unless one is given; an
# existing list on this host is kept (it may hold keys added here).
dest="$HOME/.config/labape/allowed_signers"
if [ -n "$SIGNERS" ]; then install -m 0644 "$SIGNERS" "$dest"
elif [ ! -s "$dest" ] && [ -s "$REPO/deploy/allowed_signers" ]; then install -m 0644 "$REPO/deploy/allowed_signers" "$dest"
fi

sed "s#@REPO@#$REPO#g" "$HERE/labape-update.path" > "$HOME/.config/systemd/user/labape-update.path"
sed "s#@REPO@#$REPO#g" "$HERE/labape-update.service" > "$HOME/.config/systemd/user/labape-update.service"
python3 - "$REPO/container/update/agent.json" <<'PY'
import datetime, json, socket, sys
json.dump({"installed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "host": socket.gethostname()},
          open(sys.argv[1], "w"), indent=2)
PY
systemctl --user daemon-reload
systemctl --user enable --now labape-update.path
echo "labape-update: installed; watching $REPO/container/update/request.json"
[ -s "$dest" ] || echo "labape-update: no allowed signers yet ($dest): every update will be refused until a release key is added"
