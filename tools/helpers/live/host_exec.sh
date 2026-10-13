#!/usr/bin/env bash
# host_exec.sh HOST_ID COMMAND [TIMEOUT_SECONDS]
# Run a shell command on a registered host as its LABaPe access account (passwordless sudo),
# through the worker container (the host-admin key never leaves it). Prints the output and
# exits with the command's status.
# Example: tools/helpers/lab-live.sh host_exec.sh 3 'free -h; sudo -n true && echo sudo-ok'
set -euo pipefail
[ $# -ge 2 ] || { sed -n '2,6p' "$0" >&2; exit 2; }
W="$(docker ps --format '{{.Names}}' | grep -m1 'labape-worker')"
docker exec -i -e HOST_ID="$1" -e CMD="$2" -e T="${3:-600}" "$W" python3 - <<'PY'
import os
import sys

from labape.db import session_factory
from labape.engine import hostadmin
from labape.models import KvmHost

with session_factory()() as db:
    h = db.get(KvmHost, int(os.environ["HOST_ID"]))
    r = hostadmin.for_host(h).run(os.environ["CMD"], timeout=int(os.environ["T"]))
sys.stdout.write(r.stdout.decode(errors="replace"))
sys.stderr.write(r.stderr.decode(errors="replace"))
sys.exit(r.returncode)
PY
