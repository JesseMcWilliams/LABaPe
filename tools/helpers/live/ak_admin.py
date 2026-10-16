"""An httpx client signed in to the test Authentik as akadmin, with the password the stack stores (revealed through
the API as labape-admin, which is audited). Imported by the Kerberos proof scripts; never prints the password."""
import os
import sys

sys.path.insert(0, "/app")
from contextlib import contextmanager

from labape.authentik_admin import admin_client
from oidc_login import LABAPE, login

AK_BASE = os.environ.get("AK_BASE", "https://127.0.0.1:9443")


@contextmanager
def akadmin():
    c = login("labape-admin")
    r = c.get(f"{LABAPE}/api/admin/secrets")
    pols = r.json()
    pols = pols["policies"]
    pid = next(p["id"] for p in pols if p["kind"] == "authentik_admin")
    value = c.post(f"{LABAPE}/api/admin/secrets/{pid}/reveal").json()["value"]
    with admin_client(AK_BASE, value, verify=False) as ak:
        yield ak
