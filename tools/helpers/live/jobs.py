"""List jobs through the API as a test user, with the Jobs page's filters (design §28, O1).

Usage: jobs.py [--as USER] [--facets] [filter=value ...]
Filters: environment (name contains), type (prefix), state, requested_by, since, until (ISO 8601).
Prints the count and the first rows (id, type, state, environment name, requester); --facets
prints the filter values this user may pick from.
"""
import sys

from oidc_login import LABAPE, login

args = sys.argv[1:]
user = "labape-admin"
if "--as" in args:
    i = args.index("--as")
    user = args[i + 1]
    del args[i:i + 2]
c = login(user)
if "--facets" in args:
    print(c.get(f"{LABAPE}/api/jobs/facets").json())
    sys.exit(0)
params = dict(a.split("=", 1) for a in args)
jobs = c.get(f"{LABAPE}/api/jobs", params={"limit": 500, **params}).json()
print(f"{user}: {len(jobs)} job(s) for {params or 'no filters'}")
for j in jobs[:8]:
    env = j.get("environment_name") or ""
    if j.get("environment_status") == "destroyed":
        env += " (destroyed)"
    print(f"  #{j['id']} {j['type']} {j['state']} {env} by {j['requested_by']}")
