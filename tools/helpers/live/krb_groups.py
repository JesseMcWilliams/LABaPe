"""The test Authentik's groups with their members' names, and the AD source's sync status (read-only)."""
from ak_admin import AK_BASE, akadmin

with akadmin() as c:
    for g in c.get(f"{AK_BASE}/api/v3/core/groups/", params={"page_size": 100, "include_users": "true"}).json()["results"]:
        print(f"{g['name']:34} members={[u['username'] for u in g.get('users_obj', [])]}")
    r = c.get(f"{AK_BASE}/api/v3/sources/ldap/lab2-ad/sync/status/")
    print("sync:", r.status_code, r.text[:600])
