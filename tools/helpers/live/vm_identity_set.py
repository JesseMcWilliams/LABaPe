"""Set a VM's recorded SSH keys to only one key type (a debugging aid for the browser SSH host-key check), or
re-scan all of them.

Usage (in the API container): python /tmp/vm_identity_set.py ADDRESS TYPES   (TYPES: all, or ed25519, ecdsa, rsa, or a comma list in the order to store them)
"""
import sys

from labape.db import session_factory
from labape.engine import vmidentity

address, which = sys.argv[1], sys.argv[2]
full = vmidentity.scan_ssh(address)
PREFIX = {"ed25519": "ssh-ed25519", "ecdsa": "ecdsa-", "rsa": "ssh-rsa"}
order = list(PREFIX) if which == "all" else which.split(",")
lines = [ln for t in order for ln in full.splitlines() if ln.startswith(PREFIX[t])]
with session_factory()() as db:
    vmidentity.remember(db, address, vmidentity.SSH, "\n".join(lines))
print("record now:", [l.split()[0] for l in lines])
