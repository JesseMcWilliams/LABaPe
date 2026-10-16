"""List the test Authentik's users that came from the Kerberos source (read-only)."""
from ak_admin import AK_BASE, akadmin

with akadmin() as c:
    for u in c.get(f"{AK_BASE}/api/v3/core/users/", params={"page_size": 100}).json()["results"]:
        print(f"{u['username']:30} name={u['name']!r:20} type={u['type']:10} path={u['path']:16} groups={[g['name'] for g in u.get('groups_obj', [])]}")
    print("connections:", [(x["source"]["slug"], x["identifier"]) for x in c.get(f"{AK_BASE}/api/v3/sources/user_connections/all/").json()["results"]])
