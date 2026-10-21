"""Certificates (design §65): a round trip with the real platform CA.

Usage: certs.py [list] | roundtrip | cleanup
`roundtrip` signs a 1-day certificate for zz-cert-test.<this stack's parent domain> (LABaPe makes the key), lists the
register, downloads it, revokes it with step-ca, and shows the CA's page. The private key is never printed. The
revoked test certificate stays in the register.
"""
import sys

from oidc_login import LABAPE, login

c = login("labape-admin")


def show(rows):
    for r in rows:
        print(f"  #{r['id']:<4} {r['state']:9} {r['source']:9} {r['subject']:40} by {r['requested_by']:12} {r['days_left']} days  {r['serial_hex'][:12]}")


if sys.argv[1:2] == ["roundtrip"]:
    s = c.get(f"{LABAPE}/api/certificates/settings").json()
    print("always allowed:", s["always_allowed"], "| default/max days:", s["default_days"], s["max_days"])
    domain = next((d for d in s["always_allowed"] if d.count(".") >= 1 and not d.replace(".", "").isdigit()), "jlm.abq")
    r = c.post(f"{LABAPE}/api/certificates", json={"common_name": f"zz-cert-test.{domain}", "days": 1})
    print("request:", r.status_code, "" if r.status_code == 201 else r.text[:300])
    if r.status_code == 201:
        j = r.json()
        cert = j["certificate"]
        print("signed:", cert["subject"], cert["sans"], "valid to", cert["not_after"], "| key returned:", "PRIVATE KEY" in j["key"],
              "| chain certs:", j["chain"].count("BEGIN CERTIFICATE"), "| root ok:", "BEGIN CERTIFICATE" in j["root"])
        d = c.get(f"{LABAPE}/api/certificates/{cert['id']}/download")
        print("download:", d.status_code, d.headers.get("content-disposition"), "| has private key:", "PRIVATE KEY" in d.text)
        rv = c.post(f"{LABAPE}/api/certificates/{cert['id']}/revoke", json={"reason": "live test"})
        print("revoke:", rv.status_code, rv.json().get("state") if rv.status_code == 200 else rv.text[:300])
if sys.argv[1:2] == ["cleanup"]:                       # revoke the test certificates still active
    for r in c.get(f"{LABAPE}/api/certificates", params={"scope": "all", "limit": 200}).json():
        if r["subject"].startswith("zz-cert-test.") and r["state"] != "revoked":
            print("revoke", r["id"], c.post(f"{LABAPE}/api/certificates/{r['id']}/revoke", json={"reason": "live test cleanup"}).status_code)
rows = c.get(f"{LABAPE}/api/certificates", params={"scope": "all", "limit": 50}).json()
print(f"register ({len(rows)}):")
show(rows[:20])
ca = c.get(f"{LABAPE}/api/certificates/ca").json()
print("CA:", ca["url"], "| healthy:", ca["healthy"], "| root:", (ca["root"] or {}).get("subject"), "| provisioners:", ca["provisioners"], "| counts:", ca["counts"])
