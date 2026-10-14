#!/usr/bin/env bash
# Install (or refresh) the LABaPe update agent for the current user (design §23.2):
#   deploy/update-agent/install.sh [--repo DIR] [--signers FILE] [--cli-vault FILE|none] [--vault-pass-file FILE]
# --cli-vault is the command line's vault on this host (default
# <repo>/secrets.vault.yml; none for no command line), which stores.sh reads
# when it sets up the command line's store login; --vault-pass-file is its
# password file (default ~/.labape-vault-pass).
# Needs lingering for the user (`sudo loginctl enable-linger <user>`, which
# the installer's Update.Agent step does), so the user's systemd keeps
# running without a login.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
SIGNERS=""
CLI_VAULT=""
PASS_FILE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="${2:?}"; shift ;;
    --signers) SIGNERS="${2:?}"; shift ;;
    --cli-vault) CLI_VAULT="${2:?}"; shift ;;
    --vault-pass-file) PASS_FILE="${2:?}"; shift ;;
    *) sed -n '2,11p' "$0" >&2; exit 1 ;;
  esac
  shift
done

mkdir -p "$HOME/.local/libexec" "$HOME/.config/systemd/user" "$HOME/.config/labape" "$REPO/container/update"
install -m 0755 "$HERE/labape-update.sh" "$HOME/.local/libexec/labape-update.sh"

# Who may sign releases: the repository's list unless one is given; an
# existing list on this host is kept when it names a key (it may hold keys
# added here). A list with only comments (the placeholder before a release
# key existed) is replaced.
dest="$HOME/.config/labape/allowed_signers"
has_keys() { [ -f "$1" ] && grep -qvE '^[[:space:]]*(#|$)' "$1"; }
if [ -n "$SIGNERS" ]; then install -m 0644 "$SIGNERS" "$dest"
elif ! has_keys "$dest" && [ -s "$REPO/deploy/allowed_signers" ]; then install -m 0644 "$REPO/deploy/allowed_signers" "$dest"
fi

# Where the command line's vault is (for stores.sh). An existing setting is kept unless an option is given.
envf="$HOME/.config/labape/agent.env"
if [ -n "$CLI_VAULT$PASS_FILE" ] || [ ! -f "$envf" ]; then
  { echo "# Written by deploy/update-agent/install.sh; read by container/stores/stores.sh."
    echo "LABAPE_CLI_VAULT=${CLI_VAULT:-$REPO/secrets.vault.yml}"
    echo "LABAPE_VAULT_PASS_FILE=${PASS_FILE:-$HOME/.labape-vault-pass}"; } > "$envf"
fi
sed "s#@REPO@#$REPO#g" "$HERE/labape-update.path" > "$HOME/.config/systemd/user/labape-update.path"
sed "s#@REPO@#$REPO#g" "$HERE/labape-update.service" > "$HOME/.config/systemd/user/labape-update.service"
python3 - "$REPO/container/update/agent.json" <<'PY'
import datetime, getpass, json, os, socket, sys
json.dump({"installed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "host": socket.gethostname(),
           "user": getpass.getuser(), "home": os.path.expanduser("~")},
          open(sys.argv[1], "w"), indent=2)
PY
systemctl --user daemon-reload
systemctl --user enable --now labape-update.path
echo "labape-update: installed; watching $REPO/container/update/request.json"
[ -s "$dest" ] || echo "labape-update: no allowed signers yet ($dest): every update will be refused until a release key is added"
