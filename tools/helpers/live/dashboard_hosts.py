"""Print the KVM hosts the dashboard shows (disabled hosts are left out).

Usage: dashboard_hosts.py
"""
from oidc_login import LABAPE, login

c = login("labape-admin")
print([h["name"] for h in c.get(f"{LABAPE}/api/dashboard").json()["hosts"]])
