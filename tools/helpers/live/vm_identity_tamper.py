"""Corrupt a VM's recorded ECDSA key (so the next connection must be refused), or put the real keys back.

Usage (in the API container): python /tmp/vm_identity_tamper.py ADDRESS bad|good
"""
import sys

from labape.db import session_factory
from labape.engine import vmidentity

address, mode = sys.argv[1], sys.argv[2]
with session_factory()() as db:
    value = vmidentity.scan_ssh(address)
    if mode == "bad":
        value = "\n".join(ln[:-8] + "AAAAAAAA" if ln.startswith("ecdsa-") else ln for ln in value.splitlines())
    vmidentity.remember(db, address, vmidentity.SSH, value)
print("record is", mode)
