#!/usr/bin/env bash
# vm_exec.sh ENV_ID VM COMMAND [TIMEOUT_SECONDS]
# Run a shell command on one of an environment's Linux VMs as its admin user
# (passwordless sudo), with the VM key, through the worker container (the key
# never leaves it). Prints the command's output; exits with its status.
# Example: tools/helpers/lab-live.sh vm_exec.sh 14 stack1 'sudo -n journalctl -n 20'
set -euo pipefail
[ $# -ge 3 ] || { sed -n '2,6p' "$0" >&2; exit 2; }
W="$(docker ps --format '{{.Names}}' | grep -m1 'labape-worker')"
docker exec -i -e ENV_ID="$1" -e VM="$2" -e CMD="$3" -e T="${4:-600}" "$W" python3 - <<'PY'
import os
import sys

from labape.db import session_factory
from labape.engine import vmadmin
from labape.models import Environment

with session_factory()() as db:
    env = db.get(Environment, int(os.environ["ENV_ID"]))
    _, host, _ = vmadmin._vm(db, env, os.environ["VM"])
    ssh = vmadmin._linux_ssh(db, host)
    try:
        r = ssh.run(os.environ["CMD"], timeout=int(os.environ["T"]))
    finally:
        ssh.close()
sys.stdout.write(r.stdout.decode(errors="replace"))
sys.stderr.write(r.stderr.decode(errors="replace"))
sys.exit(r.returncode)
PY
