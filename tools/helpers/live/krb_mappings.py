"""List the test Authentik's LDAP property mappings (read-only)."""
from ak_admin import AK_BASE, akadmin

with akadmin() as c:
    for m in c.get(f"{AK_BASE}/api/v3/propertymappings/source/ldap/", params={"page_size": 100}).json()["results"]:
        print(m.get("managed"), "|", m["name"])
