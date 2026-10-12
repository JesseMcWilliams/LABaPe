#!/usr/bin/env bash
# agent-request.sh <update|rollback> [tag]
# Runs on the lab host (lab-live.sh runs it there). Writes a request for the
# update agent directly, as the web interface would, waits for it to finish
# (up to 20 minutes), then shows the outcome: the agent's final state, the
# running version, the checkout, the version on standby, maintenance mode and
# the agent log's last lines. The agent refuses unsigned tags.
set -uo pipefail
[ $# -ge 1 ] || { sed -n '2,8p' "$0" >&2; exit 1; }
R="${LABAPE_LAB_REPO:-$HOME/LABaPe-web}"; R="${R/#\~/$HOME}"
U="$R/container/update"
host="$(grep -m1 '^LABAPE_HOSTNAME=' "$R/container/.env" | cut -d= -f2-)"
python3 - "$U" "$1" "${2:-}" <<'PY'
import datetime, json, os, sys, uuid
d, action, target = sys.argv[1:4]
json.dump({"id": uuid.uuid4().hex, "action": action, "target": target, "requested_by": "agent-request.sh",
           "requested_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}, open(f"{d}/.r.tmp", "w"))
os.replace(f"{d}/.r.tmp", f"{d}/request.json")
PY
sleep 5
st=""
for _ in $(seq 1 120); do
  st=$(python3 -c "import json; s=json.load(open('$U/status.json')); print(s['state'], '|', s['step'], '|', s['message'])" 2>/dev/null)
  case "$st" in running*|"") sleep 10 ;; *) break ;; esac
done
compose() { (cd "$R/container" && docker compose "$@"); }
echo "final: $st"
echo "health: $(curl -sk --resolve "$host:443:127.0.0.1" "https://$host/api/health")"
echo "running version: $(compose exec -T labape-api python3 -c 'import os; print(os.environ.get("LABAPE_VERSION"))' 2>/dev/null)"
echo "checkout: $(git -C "$R" describe --tags --always)"
echo "on standby: $([ -f "$U/previous.json" ] && python3 -c "import json; print(json.load(open('$U/previous.json'))['version'])" || echo none)"
echo "maintenance: $(compose exec -T labape-api labape maintenance status 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["active"])' 2>/dev/null)"
echo "--- agent.log (last lines):"; grep "labape-update" "$U/agent.log" | tail -12
