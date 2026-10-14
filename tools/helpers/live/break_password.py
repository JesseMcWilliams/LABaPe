"""Test secret recovery (S1/A1): give one account of an environment a random password LABaPe doesn't know,
signed in as its pair, so the stored value no longer works. Runs inside the worker container:

  docker exec -i labape-labape-worker-1 /opt/labape/venv/bin/python - ENV_ID KIND < break_password.py

KIND: windows_admin, windows_admin2, domain_admin2 (domain_admin is refused: the lab's DC is shared).
Prints only which account and hosts were changed, never a password.
"""
import io
import sys

from labape.db import session_factory
from labape.engine import vmaccess as V
from labape.models import Environment
from labape.security import get_secret

env_id, kind = int(sys.argv[1]), sys.argv[2]
if kind not in ("windows_admin", "windows_admin2", "domain_admin2"):
    raise SystemExit("refused: only windows_admin, windows_admin2 or domain_admin2")
with session_factory()() as db:
    env = db.get(Environment, env_id)
    inv = V._inventory(db, env)
    targets = V.env_targets(env, inv, kind)
    change_on = targets[:1] if kind == "domain_admin2" else targets
    account = V.RECOVERY_ADMIN if kind.endswith("2") else "Administrator"
    module = "microsoft.ad.user" if kind == "domain_admin2" else "ansible.windows.win_user"
    junk = V.new_windows_password()
    if kind == "windows_admin":            # sign in as the pair (labape-admin2), not as the account being broken
        login = {"ansible_user": V.RECOVERY_ADMIN, "ansible_password": get_secret(db, V.env_password_name(env_id, "windows_admin2"))}
    else:
        login = {}
    inv2 = {**inv, "all": {**inv["all"], "hosts": {h: {**V._hosts(inv)[h], **login} for h in change_on}}}
    r = V._ansible(inv2, ":".join(change_on), module, {"name": account, "password": "{{ new_pw }}", "update_password": "always"},
                   {"new_pw": junk}, scrub=(junk, *(v for v in login.values() if v)))
    print(f"{account} on {', '.join(change_on)}: {dict(r)} (now an unknown password)")
