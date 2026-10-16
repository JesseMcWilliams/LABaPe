"""Kerberos proof: create (or update) Authentik's Kerberos source for the lab2 domain, with SPNEGO.

Usage: krb_source.py <environment id> <DC address> [--remove]
Reads the keytab krb_dc_setup.ps1 left on the DC (base64, held in memory only, never printed), then creates the
source `lab2-kerberos` in the test Authentik with a krb5.conf that names the KDC by address (the container can't
resolve the domain). `--remove` deletes the source. Prints statuses only.
"""
import sys

import winrm

from ak_admin import AK_BASE, akadmin
from oidc_login import LABAPE, login

env_id, dc = sys.argv[1], sys.argv[2]
remove = "--remove" in sys.argv
REALM, FQDN, SLUG = "LABAPE.TEST", "auth.labape.test", "lab2-kerberos"

with akadmin() as ak:
    existing = ak.get(f"{AK_BASE}/api/v3/sources/kerberos/{SLUG}/")
    if remove:
        print("delete:", ak.delete(f"{AK_BASE}/api/v3/sources/kerberos/{SLUG}/").status_code if existing.status_code == 200 else "none there")
        raise SystemExit

    admin = login("labape-admin")
    pw = admin.post(f"{LABAPE}/api/environments/{env_id}/secrets/reveal", json={"name": "domain_admin"}).json()["value"]
    s = winrm.Session(f"https://{dc}:5986/wsman", auth=("Administrator", pw), transport="basic", server_cert_validation="ignore")
    res = s.run_ps("[Convert]::ToBase64String([IO.File]::ReadAllBytes('C:\\Windows\\Temp\\authentik.keytab'))")
    keytab = res.std_out.decode().strip()
    print("keytab read:", res.status_code, "chars", len(keytab))
    if res.status_code or not keytab:
        raise SystemExit("could not read the keytab")

    def pk(path, **params):
        r = ak.get(f"{AK_BASE}/api/v3/{path}", params=params).json()["results"]
        return r[0]["pk"] if r else None

    mappings = [m["pk"] for m in ak.get(f"{AK_BASE}/api/v3/propertymappings/source/kerberos/").json()["results"]]
    body = {
        "name": "lab2 Windows sign-on", "slug": SLUG, "enabled": True, "promoted": False,
        "realm": REALM, "user_property_mappings": mappings,
        "authentication_flow": pk("flows/instances/", slug="default-source-authentication"),
        "enrollment_flow": pk("flows/instances/", slug="default-source-enrollment"),
        "user_matching_mode": "username_link", "group_matching_mode": "identifier",
        "krb5_conf": f"[libdefaults]\n default_realm = {REALM}\n dns_lookup_kdc = false\n dns_lookup_realm = false\n"
                     f" rdns = false\n[realms]\n {REALM} = {{\n  kdc = {dc}\n  admin_server = {dc}\n }}\n"
                     f"[domain_realm]\n .labape.test = {REALM}\n labape.test = {REALM}\n",
        "sync_users": False, "spnego_server_name": f"HTTP@{FQDN}", "spnego_keytab": keytab,
    }
    if existing.status_code == 200:
        r = ak.put(f"{AK_BASE}/api/v3/sources/kerberos/{SLUG}/", json=body)
    else:
        r = ak.post(f"{AK_BASE}/api/v3/sources/kerberos/", json=body)
    print("source:", r.status_code, r.text[:400] if r.status_code >= 300 else "ok")
    print("mappings:", len(mappings), "flows:", body["authentication_flow"] is not None, body["enrollment_flow"] is not None)
    s.run_ps("Remove-Item C:\\Windows\\Temp\\authentik.keytab -ErrorAction SilentlyContinue")
