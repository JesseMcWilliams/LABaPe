"""The monitoring view (design §30): open and recently cleared alerts, and each stack host's checks.

Usage: monitoring.py [--containers]
Signs in as labape-admin.
"""
import sys

from oidc_login import LABAPE, login

c = login("labape-admin")
m = c.get(f"{LABAPE}/api/dashboard/monitoring").json()
print(f"open alerts: {len(m['open'])}")
for a in m["open"]:
    print(f"  [{a['level']}] {a['message']} (since {a['started_at'][:19]})")
for a in m["recent"][:5]:
    print(f"  cleared: {a['message']} ({a['cleared_at'][:19]})")
for s in m["stacks"]:
    st = s["status"]
    print(f"stack #{s['id']} {s['name']} self={s['self']} url={s['stack_url'] or '-'} checked={str(s['checked_at'])[:19]} "
          f"error={s['error'][:120] or '-'}")
    if st.get("peer"):
        p = st["peer"]
        print(f"  peer: ok={p['ok']} ms={p['ms']} failures={p['failures']} cert_expires={str(p.get('cert_expires'))[:19]}")
    if st.get("self"):
        me = st["self"]
        print(f"  self: backup={me.get('backup')} certs={ {k: str(v.get('expires'))[:10] for k, v in me.get('certs', {}).items()} } "
              f"databases={me.get('databases')}")
    cs = st.get("containers") or []
    print(f"  containers: {len(cs)} ({sum(1 for x in cs if x['state'] == 'running')} running), volumes: {len(st.get('volumes') or [])}")
    if "--containers" in sys.argv:
        for x in cs:
            print(f"    {x['name']:40} {x['state']:10} {x.get('health') or '-':10} restarts={x['restarts']}")
