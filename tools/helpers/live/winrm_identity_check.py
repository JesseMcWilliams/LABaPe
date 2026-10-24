"""Question 198 on a real Windows VM: WinRM commands are held to the recorded certificate.

Usage (in the API container): python /tmp/winrm_identity_check.py ENV_ID VM
Runs `hostname` with the recorded fingerprint, with a wrong one (must be refused), and after `forget` (learns the
certificate again; it must equal the original). Statuses only.
"""
import sys

from labape.db import session_factory
from labape.engine import vmexec, vmidentity
from labape.models import Environment, VmIdentity

env_id, vm = int(sys.argv[1]), sys.argv[2]
with session_factory()() as db:
    env = db.get(Environment, env_id)
    address = vmexec.plan(db, env, vm).target["params"]["hostname"]
    original = vmidentity.trusted(db, address, vmidentity.WINRM)
    print("record       :", (original or "none")[:16] + "...")

    def run(label):
        try:
            r = vmexec.run(db, env, vm, "hostname")
            print(f"{label:13}: exit={r.exit_code} out={r.stdout.strip()!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"{label:13}: {type(exc).__name__}: {str(exc)[:170]}")

    run("right record")
    row = db.query(VmIdentity).filter_by(address=address, kind="winrm").one()
    row.value = "00" * 32
    db.commit()
    run("wrong record")
    vmidentity.forget(db, address=address)
    run("after forget")
    again = vmidentity.trusted(db, address, vmidentity.WINRM)
    print("learned again:", again is not None, "| same as the original:", again == original)
