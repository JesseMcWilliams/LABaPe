#!/usr/bin/env bash
# Run the backend tests against PostgreSQL (what production uses) instead of SQLite.
#
# Usage: tools/helpers/test-postgres.sh [pytest args and paths]     (no arguments: all of app/tests)
#        e.g. tools/helpers/test-postgres.sh app/tests/test_files.py -x
#
# A throwaway postgres container is started on the lab host (LABAPE_LAB_SSH=user@host, LABAPE_LAB_KEY optional),
# reached through an SSH tunnel; set LABAPE_LOCAL_DOCKER=1 to start it with a local Docker or Podman instead.
# PYTHON is the interpreter with `pip install -e app pytest` done (default: python3, or python).
# The container and the tunnel are removed afterwards. Nothing of the lab's own databases is touched.
set -euo pipefail
IMAGE="docker.io/library/postgres:17.11-alpine"
NAME="labape-pgtest-$$"
PORT="${LABAPE_PGTEST_PORT:-55432}"
PY="${PYTHON:-$(command -v python3 || command -v python)}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SSH_KEY=()
[ -n "${LABAPE_LAB_KEY:-}" ] && SSH_KEY=(-i "$LABAPE_LAB_KEY")

cleanup() {
  [ -n "${TUNNEL_PID:-}" ] && kill "$TUNNEL_PID" 2>/dev/null || true
  if [ "${LABAPE_LOCAL_DOCKER:-}" = 1 ]; then
    "${RT:-docker}" rm -f "$NAME" >/dev/null 2>&1 || true
  else
    ssh "${SSH_KEY[@]}" -o BatchMode=yes "$LABAPE_LAB_SSH" "docker rm -f $NAME" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

RUN="run -d --name $NAME -e POSTGRES_PASSWORD=pgtest -e POSTGRES_USER=pgtest -e POSTGRES_DB=pgtest -p 127.0.0.1:$PORT:5432 $IMAGE"
if [ "${LABAPE_LOCAL_DOCKER:-}" = 1 ]; then
  RT="$(command -v docker || command -v podman)"
  "$RT" $RUN >/dev/null
else
  : "${LABAPE_LAB_SSH:?set LABAPE_LAB_SSH (user@host), or LABAPE_LOCAL_DOCKER=1}"
  ssh "${SSH_KEY[@]}" -o BatchMode=yes "$LABAPE_LAB_SSH" "docker $RUN" >/dev/null
  ssh "${SSH_KEY[@]}" -o BatchMode=yes -N -L "$PORT:127.0.0.1:$PORT" "$LABAPE_LAB_SSH" &
  TUNNEL_PID=$!
fi

# Wait until the database itself accepts a login (the port opens before initdb has finished).
"$PY" - <<PYEOF
import sys, time
import psycopg
for _ in range(90):
    try:
        psycopg.connect("host=127.0.0.1 port=$PORT user=pgtest password=pgtest dbname=pgtest", connect_timeout=2).close()
        sys.exit(0)
    except Exception:
        time.sleep(1)
sys.exit("PostgreSQL did not come up")
PYEOF

export LABAPE_TEST_DATABASE_URL="postgresql+psycopg://pgtest:pgtest@127.0.0.1:$PORT/pgtest"
cd "$ROOT"
[ $# -eq 0 ] && set -- app/tests
"$PY" -m pytest -q -p no:cacheprovider "$@"
