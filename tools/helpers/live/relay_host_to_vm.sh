#!/usr/bin/env bash
# relay_host_to_vm.sh HOST_ID SRC_PATH ENV_ID VM DEST_PATH
# Copy one file from a registered host (read with sudo, over its access account) to an
# environment's Linux VM (written 0600 as the VM's admin user), through the worker container:
# for hosts and VMs that can't reach each other. The bytes never leave the worker; only the
# size and SHA-256 are printed. Example (a restore drill, design §33):
#   tools/helpers/lab-live.sh relay_host_to_vm.sh 3 /data/backups/stack/X.tar.sealed 17 restore1 restore/X.tar.sealed
set -euo pipefail
[ $# -eq 5 ] || { sed -n '2,8p' "$0" >&2; exit 2; }
W="$(docker ps --format '{{.Names}}' | grep -m1 'labape-worker')"
docker exec -i -e HOST_ID="$1" -e SRC="$2" -e ENV_ID="$3" -e VM="$4" -e DEST="$5" "$W" python3 - <<'PY'
import hashlib
import os
import shlex
import sys

from labape.db import session_factory
from labape.engine import hostadmin, vmadmin
from labape.models import Environment, KvmHost

with session_factory()() as db:
    r = hostadmin.for_host(db.get(KvmHost, int(os.environ["HOST_ID"]))).run(
        f"sudo cat {shlex.quote(os.environ['SRC'])}", timeout=600)
    if r.returncode != 0:
        sys.exit(f"reading {os.environ['SRC']}: {r.stderr.decode(errors='replace').strip()}")
    data = r.stdout
    _, host, _ = vmadmin._vm(db, db.get(Environment, int(os.environ["ENV_ID"])), os.environ["VM"])
    ssh = vmadmin._linux_ssh(db, host)
    dest = shlex.quote(os.environ["DEST"])
    try:
        w = ssh.run(f"umask 077 && mkdir -p \"$(dirname {dest})\" && cat > {dest} && sha256sum {dest}", input=data,
                    timeout=600)
    finally:
        ssh.close()
    if w.returncode != 0:
        sys.exit(f"writing {os.environ['DEST']}: {w.stderr.decode(errors='replace').strip()}")
    ok = w.stdout.decode().split()[0] == hashlib.sha256(data).hexdigest()
    print(f"{len(data)} bytes, sha256 {hashlib.sha256(data).hexdigest()[:16]}, {'matches' if ok else 'DIFFERS'} on the VM")
    sys.exit(0 if ok else 1)
PY
