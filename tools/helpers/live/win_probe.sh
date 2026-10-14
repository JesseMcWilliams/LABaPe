#!/usr/bin/env bash
# win_probe.sh ENV_ID VM
# Which of LABaPe's stored passwords a Windows member VM's Administrator takes
# (A2): the environment's, the VM's own, the VM's pending one. Signs in with
# each through the worker container; prints ok/rejected per name, never values.
# Then, signed in with one that works, lists the Ansible async results on the
# VM (the last detached changes) without their values.
set -euo pipefail
[ $# -ge 2 ] || { sed -n '2,7p' "$0" >&2; exit 2; }
W="$(docker ps --format '{{.Names}}' | grep -m1 'labape-worker')"
docker exec -i -e ENV_ID="$1" -e VM="$2" "$W" python3 - <<'PY'
import os

from labape import vm_passwords
from labape.db import session_factory
from labape.engine import vmaccess
from labape.models import Environment
from labape.security import get_secret

with session_factory()() as db:
    env = db.get(Environment, int(os.environ["ENV_ID"]))
    vm = os.environ["VM"]
    inv = vmaccess._inventory(db, env)
    names = {
        "environment windows_admin": vmaccess.env_password_name(env.id, "windows_admin"),
        "vm windows_admin": vm_passwords.name(env.id, vm, "windows_admin"),
        "vm windows_admin.pending": vm_passwords.name(env.id, vm, "windows_admin.pending"),
    }
    good = ""
    for label, n in names.items():
        pw = get_secret(db, n) or ""
        if not pw:
            print(f"{label}: (none stored)")
            continue
        r = vmaccess.probe(inv, [vm], None, pw)
        print(f"{label}: {r.get(vm)} {r.errors.get(vm, '')[:120]}")
        if r.get(vm) in ("ok", "changed") and not good:
            good = pw
    if good:
        import subprocess, tempfile, yaml
        from pathlib import Path
        as_ = vmaccess._per_host(inv, [vm], {vm: {"ansible_password": good}})
        ps = ("Get-ChildItem $env:USERPROFILE\\.ansible_async -ErrorAction SilentlyContinue | "
              "Sort-Object LastWriteTime | Select-Object -Last 4 | ForEach-Object { "
              "$j = Get-Content $_.FullName -Raw | ConvertFrom-Json; "
              "'{0} {1} started={2} finished={3} failed={4} changed={5} msg={6}' -f "
              "$_.Name, $_.LastWriteTime, $j.started, $j.finished, $j.failed, $j.changed, $j.msg }")
        with tempfile.TemporaryDirectory() as d:
            inv_file, play = Path(d) / "inv.yml", Path(d) / "play.yml"
            inv_file.write_text(yaml.safe_dump(as_)); os.chmod(inv_file, 0o600)
            play.write_text(yaml.safe_dump([{"hosts": vm, "gather_facts": False, "tasks": [
                {"ansible.windows.win_shell": ps, "register": "r"},
                {"ansible.builtin.debug": {"var": "r.stdout_lines"}}]}]))
            out = subprocess.run(["ansible-playbook", "-i", str(inv_file), str(play)], capture_output=True, text=True,
                                 env={**os.environ, "ANSIBLE_NOCOLOR": "1", "ANSIBLE_HOST_KEY_CHECKING": "False"}).stdout
        print("async results:")
        print(out.replace(good, "***")[-2500:])
PY
