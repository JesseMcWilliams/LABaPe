"""Print the Kerberos source's fields in the test Authentik, and its existing sources (read-only)."""
from ak_admin import AK_BASE, akadmin

with akadmin() as c:
    spec = c.get(f"{AK_BASE}/api/v3/schema/", params={"format": "json"}).json()
    comp = spec["components"]["schemas"]["KerberosSourceRequest"]
    for name, p in comp["properties"].items():
        print(f"{name:40} {p.get('type', p.get('$ref', '')):10} {p.get('description', '')[:100]!r}")
    print("required:", comp.get("required"))
    print("existing:", [(s["slug"], s["component"]) for s in c.get(f"{AK_BASE}/api/v3/sources/all/").json()["results"]])
