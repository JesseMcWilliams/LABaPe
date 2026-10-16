"""Kerberos proof, groups: give Authentik the domain's users and groups so a Kerberos sign-in carries AD groups.

Usage: krb_ldap.py <environment id> <DC address> [--remove]
On the DC: the groups lab2-admins, lab2-deployers and lab2-viewers (named so they don't merge with the test
Authentik's own labape-* groups; LABaPe maps them to roles with role bindings), `jesse` in lab2-admins, and a bind account `svc-authentik-ldap` with a random password that is
only ever sent to Authentik. In Authentik: the LDAP source `lab2-ad` (users and groups, sAMAccountName as the
user name) and the Kerberos source matching users by name, so `jesse` signs in with Kerberos as the synced
user and carries its groups. Prints statuses, never the password. `--remove` deletes the LDAP source.
"""
import secrets
import sys
import time

import winrm

from ak_admin import AK_BASE, akadmin
from oidc_login import LABAPE, login

env_id, dc = sys.argv[1], sys.argv[2]
SLUG, BASE_DN, BIND = "lab2-ad", "DC=labape,DC=test", "svc-authentik-ldap"

with akadmin() as ak:
    if "--remove" in sys.argv:
        print("delete:", ak.delete(f"{AK_BASE}/api/v3/sources/ldap/{SLUG}/").status_code)
        raise SystemExit

    admin = login("labape-admin")
    pw = admin.post(f"{LABAPE}/api/environments/{env_id}/secrets/reveal", json={"name": "domain_admin"}).json()["value"]
    bind_pw = secrets.token_urlsafe(24) + "aA1!"
    PS = """
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
foreach ($old in 'labape-admins', 'labape-deployers', 'labape-viewers') { Get-ADGroup -Filter "Name -eq '$old'" | Remove-ADGroup -Confirm:$false }
foreach ($g in 'lab2-admins', 'lab2-deployers', 'lab2-viewers') {
    if (-not (Get-ADGroup -Filter "Name -eq '$g'")) { New-ADGroup -Name $g -GroupScope Global -Path 'CN=Users,%(base)s'; "group: created $g" }
}
Add-ADGroupMember -Identity lab2-admins -Members jesse -ErrorAction SilentlyContinue
$secure = ConvertTo-SecureString '%(pw)s' -AsPlainText -Force
if (-not (Get-ADUser -Filter "SamAccountName -eq '%(bind)s'")) {
    New-ADUser -Name '%(bind)s' -SamAccountName '%(bind)s' -AccountPassword $secure -Enabled $true -PasswordNeverExpires $true `
        -Description 'LABaPe Kerberos proof: Authentik reads users and groups'
    'account: created %(bind)s'
} else { Set-ADAccountPassword -Identity '%(bind)s' -Reset -NewPassword $secure; 'account: password reset' }
(Get-ADGroupMember lab2-admins | Select-Object -ExpandProperty SamAccountName) -join ','
""" % {"base": BASE_DN, "pw": bind_pw, "bind": BIND}
    s = winrm.Session(f"https://{dc}:5986/wsman", auth=("Administrator", pw), transport="basic", server_cert_validation="ignore")
    res = s.run_ps(PS)
    print(res.std_out.decode(errors="replace").strip())
    if res.status_code:
        raise SystemExit("DC step failed: " + res.std_err.decode(errors="replace")[:600])

    # The user the Kerberos proof created would collide with the synced one: remove it (test data only).
    for u in ak.get(f"{AK_BASE}/api/v3/core/users/", params={"username": "jesse"}).json()["results"]:
        if u["path"].startswith("goauthentik.io/sources/lab2-kerberos"):
            print("removed the Kerberos-created user:", ak.delete(f"{AK_BASE}/api/v3/core/users/{u['pk']}/").status_code)

    def mappings(kind):
        r = ak.get(f"{AK_BASE}/api/v3/propertymappings/source/{kind}/", params={"page_size": 100}).json()["results"]
        return r

    wanted = ("default-name", "ms-samaccountname", "default-mail", "ms-userprincipalname")
    user_maps = [m["pk"] for m in mappings("ldap") if (m.get("managed") or "").rsplit("/", 1)[-1] in wanted]
    group_maps = [m["pk"] for m in mappings("ldap") if m["name"] == "labape: AD group name"]
    if not group_maps:
        # This Authentik ships no group mapping: the group's name is its cn.
        r = ak.post(f"{AK_BASE}/api/v3/propertymappings/source/ldap/", json={
            "name": "labape: AD group name", "expression": 'v = ldap.get("cn")\nreturn {"name": v[0] if isinstance(v, list) else v}'})
        print("group mapping:", r.status_code, r.text[:300] if r.status_code >= 300 else "created")
        group_maps = [r.json()["pk"]] if r.status_code < 300 else []
    flow = lambda slug: (ak.get(f"{AK_BASE}/api/v3/flows/instances/", params={"slug": slug}).json()["results"] or [{}])[0].get("pk")
    body = {
        "name": "lab2 Active Directory", "slug": SLUG, "enabled": True, "promoted": False,
        "authentication_flow": flow("default-source-authentication"), "enrollment_flow": flow("default-source-enrollment"),
        "user_matching_mode": "username_link", "group_matching_mode": "identifier",
        "server_uri": f"ldap://{dc}", "bind_cn": f"{BIND}@labape.test", "bind_password": bind_pw, "start_tls": False,
        "base_dn": BASE_DN, "additional_user_dn": "CN=Users", "additional_group_dn": "CN=Users",
        "user_object_filter": "(&(objectClass=user)(!(objectClass=computer)))", "group_object_filter": "(objectClass=group)",
        "group_membership_field": "member", "user_membership_attribute": "distinguishedName",
        "object_uniqueness_field": "objectSid", "sync_users": True, "sync_groups": True,
        "user_property_mappings": user_maps, "group_property_mappings": group_maps,
    }
    exists = ak.get(f"{AK_BASE}/api/v3/sources/ldap/{SLUG}/").status_code == 200
    r = (ak.put(f"{AK_BASE}/api/v3/sources/ldap/{SLUG}/", json=body) if exists else ak.post(f"{AK_BASE}/api/v3/sources/ldap/", json=body))
    print("ldap source:", r.status_code, r.text[:500] if r.status_code >= 300 else "ok", f"user maps {len(user_maps)}, group maps {len(group_maps)}")
    if r.status_code < 300:
        time.sleep(40)                                   # the first sync runs on creation
        for u in ak.get(f"{AK_BASE}/api/v3/core/users/", params={"page_size": 100}).json()["results"]:
            if u["path"].startswith("goauthentik.io/sources/lab2-ad"):
                print(f"  synced user {u['username']:16} groups={[g['name'] for g in u.get('groups_obj', [])]}")
