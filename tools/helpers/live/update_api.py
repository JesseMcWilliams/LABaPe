"""Show what the update API reports, and optionally offer it a tag.

Usage: update_api.py [tag]
Prints the current version, latest release and pre-release, database
revision, standby version and agent state. With a tag, asks the API to
update to it (an unpublished tag is refused, which is the point of the check).
"""
import json
import sys

from oidc_login import LABAPE, login

admin = login("labape-admin")
u = admin.get(f"{LABAPE}/api/admin/update").json()
print(json.dumps({k: u[k] for k in ("current", "release", "prerelease", "db_revision", "previous")}, default=str))
print("agent:", json.dumps(u["agent"]))
if len(sys.argv) > 1:
    r = admin.post(f"{LABAPE}/api/admin/update", json={"target": sys.argv[1]})
    body = r.json()
    print("offer", sys.argv[1], "->", r.status_code, body.get("errors", [{}])[0].get("message", body))
