"""The monitoring view (design §30): open and recently cleared alerts, and each stack host's checks.

Usage: monitoring.py [--containers]
       monitoring.py ack ALERT_ID | unack ALERT_ID       (design §31)
       monitoring.py silence ALERT_ID HOURS [REASON] | end SILENCE_ID
Signs in as labape-admin.
"""
import sys

from oidc_login import LABAPE, login

c = login("labape-admin")
cmd = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else ""
if cmd in ("ack", "unack"):
    r = c.post(f"{LABAPE}/api/dashboard/monitoring/alerts/{sys.argv[2]}/ack", json={"ack": cmd == "ack"})
    sys.exit(print(r.status_code, r.text[:300]))
if cmd == "silence":
    r = c.post(f"{LABAPE}/api/dashboard/monitoring/silences",
               json={"alert_id": int(sys.argv[2]), "hours": int(sys.argv[3]), "reason": " ".join(sys.argv[4:])})
    sys.exit(print(r.status_code, r.text[:300]))
if cmd == "end":
    r = c.delete(f"{LABAPE}/api/dashboard/monitoring/silences/{sys.argv[2]}")
    sys.exit(print(r.status_code, r.text[:300]))
m = c.get(f"{LABAPE}/api/dashboard/monitoring").json()
print(f"open alerts: {len(m['open'])}{' (maintenance: announcements held)' if m.get('quiet') else ''}")
for a in m["open"]:
    print(f"  #{a['id']} [{a['level']}] {a['message']} (since {a['started_at'][:19]}; announced {str(a.get('announced_at'))[:19]}"
          f"{'; acked by ' + a['acked_by'] if a.get('acked_at') else ''}"
          f"{'; silenced until ' + str(a['silenced_until'])[:19] if a.get('silenced_until') else ''})")
for s in m.get("silences", []):
    print(f"  silence #{s['id']}: {s['message']} until {str(s['until'])[:19]} by {s['created_by']} ({s['reason']})")
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
