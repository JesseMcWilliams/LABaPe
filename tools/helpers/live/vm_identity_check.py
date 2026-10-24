"""Question 198 on a real Linux VM: commands check its SSH host key against the record.

Usage (in the API container): python /tmp/vm_identity_check.py ENV_ID VM
Runs `hostname` three ways: with the recorded key, with a wrong record (must be refused, with the hint), and after
`forget` (learns the key again; it must equal the original). Restores the record. Statuses only.
"""
import sys

from labape.db import session_factory
from labape.engine import vmexec, vmidentity
from labape.models import Environment, VmIdentity

env_id, vm = int(sys.argv[1]), sys.argv[2]
with session_factory()() as db:
    env = db.get(Environment, env_id)
    pl = vmexec.plan(db, env, vm)
    address = pl.target["params"]["hostname"]
    original = vmidentity.trusted(db, address, vmidentity.SSH)
    print("record       :", (original or "").count("\n") + 1 if original else None, "key line(s) for", address)

    def run(label):
        r = vmexec.run(db, env, vm, "hostname")
        print(f"{label:13}: exit={r.exit_code} out={r.stdout.strip()!r} err={r.stderr.strip()[:160]!r}")
        return r

    run("right record")
    row = db.query(VmIdentity).filter_by(address=address, kind="ssh").one()
    row.value = "\n".join(l[:-6] + "AAAAAA" if l.startswith("ssh-ed25519") else l for l in original.splitlines())
    db.commit()
    bad = run("wrong record")
    print("refused      :", bad.exit_code != 0 and "recorded a different" in bad.stderr)
    vmidentity.forget(db, address=address)
    run("after forget")
    again = vmidentity.trusted(db, address, vmidentity.SSH)
    print("learned again:", again is not None, "| same as the original:", again == original)
    if again != original and original:
        vmidentity.remember(db, address, vmidentity.SSH, original, env_id)
        print("record restored")
