"""host_pools.py [refresh]: each host's drive pools as the Drive Pools page sees them (read-only; `refresh` reads the facts first)."""
import sys

from oidc_login import LABAPE, login

c = login("labape-admin")
for h in c.get(f"{LABAPE}/api/hosts").json():
    if len(sys.argv) > 1 and sys.argv[1] == "refresh":
        c.post(f"{LABAPE}/api/hosts/{h['id']}/admin/refresh")
    v = c.get(f"{LABAPE}/api/hosts/{h['id']}/admin").json()
    pools = v.get("pools") or []
    print(h["id"], h["name"], "pools:", [(p["id"], p["level"], p["state"], len(p["members"])) for p in pools],
          "free drives:", [d["path"] for d in v.get("pool_candidates") or []][:5])
    print("   md raw:", list((v["facts"].get("md") or {}).keys()), "zfs:", [z["name"] for z in v["facts"].get("zfs") or []],
          "vgs:", [g["vg_name"] for g in (v["facts"].get("lvm") or {}).get("vgs") or []])
