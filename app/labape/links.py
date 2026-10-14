"""Linked stacks (design §34, batch L1): two LABaPe stacks that read each other's summary.

Linking takes a one-time code. Stack A makes one (`invite`): it carries A's address, the SHA-256
fingerprint of A's platform CA root and a random secret, and is handed to B's admin out of band.
B (`join`) fetches A's CA root, checks it against the fingerprint, and from then on talks to A over
HTTPS checked against that root. B sends the code, its own address and CA root, and a fresh token
for A to call B with; A checks the code (unused, unexpired), records B, and answers with the token
B is to call A with. Each side keeps the token it calls with in its secret store and only a hash of
the token it's called with. A code is good once, for an hour.

What a linked stack can read is `summary()`: read-only (question 147), about the stack as a whole
(its hosts, environments, jobs, open alerts, totals). Every stack polls its links every minute and
keeps the last summary, so one that stops answering shows its last data as stale (question 13 of
the review); after Settings → Monitoring's peer failures it's an alert (§30).
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import secrets as pysecrets
import ssl
from urllib.parse import urlsplit

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import ca, maintenance_mode, version
from .config import get_settings
from .models import (Environment, HostFacts, HostStats, Job, KvmHost, MonitorAlert, Secret, StackLink,
                     StackLinkInvite, utcnow)
from .security import get_secret, put_secret

CODE_PREFIX = "LABAPE-LINK-1:"
INVITE_TTL = dt.timedelta(hours=1)
STALE_AFTER = dt.timedelta(minutes=3)
TIMEOUT = 15.0


class LinkError(Exception):
    pass


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def fingerprint(pem: str) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    try:
        return x509.load_pem_x509_certificate(pem.encode()).fingerprint(hashes.SHA256()).hex()
    except ValueError as exc:
        raise LinkError("that isn't a CA certificate") from exc


def own_url() -> str:
    return get_settings().public_url.rstrip("/")


def _name(url: str) -> str:
    return urlsplit(url).hostname or url


def _secret_name(link_id: int) -> str:
    return f"link.{link_id}.outbound"


def _drop_secret(db: Session, name: str) -> None:
    row = db.get(Secret, name)
    if row is None:
        return
    if row.store != "builtin":
        from .stores import open_store
        try:
            open_store(db, row.store).delete(name)
        except Exception:  # noqa: BLE001 — the row goes anyway; the store keeps an orphan
            pass
    db.delete(row)
    db.commit()


def client(pem: str) -> httpx.Client:
    """HTTPS to a peer: its platform CA root (pinned at link time) plus the system roots, for a peer
    whose web certificate comes from a public CA."""
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(cadata=pem)
    return httpx.Client(verify=ctx, timeout=TIMEOUT)


# --- making a link -----------------------------------------------------------------------------

def invite(db: Session, actor: str, now: dt.datetime | None = None) -> dict:
    now = now or utcnow()
    secret = pysecrets.token_urlsafe(24)
    db.add(StackLinkInvite(code_hash=_sha(secret), created_by=actor, created_at=now, expires_at=now + INVITE_TTL))
    db.commit()
    payload = {"u": own_url(), "f": fingerprint(ca.root_pem()), "c": secret}
    code = CODE_PREFIX + base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    return {"code": code, "expires_at": now + INVITE_TTL}


def parse_code(code: str) -> dict:
    code = "".join(code.split())
    if not code.startswith(CODE_PREFIX):
        raise LinkError("that isn't a LABaPe link code")
    raw = code[len(CODE_PREFIX):]
    try:
        p = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        url, fp, secret = str(p["u"]).rstrip("/"), str(p["f"]).lower(), str(p["c"])
    except (ValueError, KeyError, TypeError) as exc:
        raise LinkError("the link code is damaged") from exc
    if not url.startswith("https://") or len(fp) != 64 or not secret:
        raise LinkError("the link code is damaged")
    return {"url": url, "fingerprint": fp, "secret": secret}


def fetch_ca(url: str) -> str:
    """The peer's platform CA root, before it can be checked: the caller compares its fingerprint."""
    try:
        r = httpx.get(f"{url}/api/link/ca", verify=False, timeout=TIMEOUT)  # noqa: S501 — pinned by fingerprint
        r.raise_for_status()
        return r.json()["pem"]
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise LinkError(f"{url} didn't answer as a LABaPe stack: {exc}") from exc


def join(db: Session, code: str, actor: str) -> StackLink:
    """This stack's side of linking with the stack that made `code`."""
    c = parse_code(code)
    if c["url"] == own_url():
        raise LinkError("that link code is this stack's own")
    if db.scalar(select(StackLink).where(StackLink.url == c["url"])):
        raise LinkError(f"already linked with {c['url']}")
    pem = fetch_ca(c["url"])
    if not hmac.compare_digest(fingerprint(pem), c["fingerprint"]):
        raise LinkError(f"{c['url']}'s CA isn't the one in the link code; not linking")
    mine = pysecrets.token_urlsafe(32)                 # the peer calls this stack with it
    try:
        with client(pem) as h:
            r = h.post(f"{c['url']}/api/link/join", json={"code": c["secret"], "url": own_url(), "ca_pem": ca.root_pem(),
                                                          "token": mine})
    except httpx.HTTPError as exc:
        raise LinkError(f"{c['url']}: {exc}") from exc
    if r.status_code != 200:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = r.text[:200]
        raise LinkError(f"{c['url']} refused: {detail}")
    theirs = r.json()["token"]
    link = StackLink(name=_name(c["url"]), url=c["url"], ca_pem=pem, ca_fingerprint=c["fingerprint"],
                     inbound_hash=_sha(mine), created_by=actor)
    db.add(link)
    db.commit()
    put_secret(db, _secret_name(link.id), theirs)
    return link


def accept(db: Session, code_secret: str, url: str, ca_pem: str, token: str, now: dt.datetime | None = None) -> dict:
    """The inviting side: check the code, record the peer, hand back the token it calls with."""
    now = now or utcnow()
    url = url.rstrip("/")
    inv = db.scalar(select(StackLinkInvite).where(StackLinkInvite.code_hash == _sha(code_secret)))
    exp = None if inv is None else (inv.expires_at if inv.expires_at.tzinfo
                                    else inv.expires_at.replace(tzinfo=dt.timezone.utc))
    if inv is None or inv.used_at is not None or exp < now:
        raise LinkError("the link code isn't valid (used, expired, or never made here)")
    if not url.startswith("https://") or url == own_url():
        raise LinkError("a linked stack needs its own https address")
    if db.scalar(select(StackLink).where(StackLink.url == url)):
        raise LinkError(f"already linked with {url}")
    fp = fingerprint(ca_pem)
    theirs = pysecrets.token_urlsafe(32)               # the peer calls this stack with it
    link = StackLink(name=_name(url), url=url, ca_pem=ca_pem, ca_fingerprint=fp, inbound_hash=_sha(theirs),
                     created_by=f"link code from {inv.created_by}")
    inv.used_at, inv.used_by = now, url
    db.add(link)
    db.commit()
    put_secret(db, _secret_name(link.id), token)
    return {"token": theirs, "name": _name(own_url()), "url": own_url()}


def caller(db: Session, authorization: str | None) -> StackLink | None:
    """The linked stack a request's `Authorization: Bearer <token>` belongs to."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    h = _sha(authorization[7:].strip())
    for link in db.scalars(select(StackLink)):
        if hmac.compare_digest(link.inbound_hash, h):
            return link
    return None


def remove(db: Session, link: StackLink, tell_peer: bool = True) -> None:
    """Unlink: the peer is told (best effort, so it drops its side too), then this side goes."""
    if tell_peer:
        token = get_secret(db, _secret_name(link.id))
        if token:
            try:
                with client(link.ca_pem) as h:
                    h.post(f"{link.url}/api/link/leave", headers={"Authorization": f"Bearer {token}"})
            except httpx.HTTPError:
                pass
    _drop_secret(db, _secret_name(link.id))
    db.delete(link)
    db.commit()


# --- what a peer can read ------------------------------------------------------------------------

def summary(db: Session) -> dict:
    """This stack as a whole, for the stacks linked with it (read-only)."""
    now = utcnow()
    stats = {r.kvm_host_id: r for r in db.scalars(select(HostStats))}
    facts = {r.kvm_host_id: r.facts or {} for r in db.scalars(select(HostFacts))}
    hosts = []
    tot = {"cpus": 0, "memory_total_mb": 0, "memory_used_mb": 0, "disk_total_gb": 0.0, "disk_free_gb": 0.0,
           "vms_running": 0, "vms_defined": 0}
    for h in db.scalars(select(KvmHost).order_by(KvmHost.name)):
        row = stats.get(h.id)
        s = (row.stats if row else None) or {}
        vm = (s.get("storage") or {}).get("vm") or {}
        used = (s["memory_total_mb"] - s["memory_available_mb"]) if s.get("memory_total_mb") else None
        # The machine behind the record, so the overall view counts a host two stacks share once
        # (design §35): its own host name when its facts were read, else the libvirt address.
        machine = (((facts.get(h.id) or {}).get("os") or {}).get("hostname") or
                   urlsplit(h.libvirt_uri).hostname or urlsplit(own_url()).hostname or h.name).lower()
        out = {"id": h.id, "name": h.name, "machine": machine, "role": h.role, "enabled": h.enabled,
               "collected_at": row.collected_at.isoformat() if row and row.collected_at else None,
               "cpus": s.get("cpus"), "cpu_pct": s.get("cpu_pct"), "memory_total_mb": s.get("memory_total_mb"),
               "memory_used_mb": used, "disk_total_gb": vm.get("total_gb"), "disk_free_gb": vm.get("free_gb"),
               "vms_running": s.get("vms_running"), "vms_defined": s.get("vms_defined")}
        hosts.append(out)
        if h.enabled:
            for k in tot:
                v = used if k == "memory_used_mb" else out.get(k)
                tot[k] += v or 0
    envs = [{"id": e.id, "name": e.name, "status": e.status, "host": e.kvm_host.name if e.kvm_host else "",
             "owner": e.created_by} for e in db.scalars(select(Environment).order_by(Environment.name)).all()
            if e.status != "destroyed"]
    by_status: dict[str, int] = {}
    for e in envs:
        by_status[e["status"]] = by_status.get(e["status"], 0) + 1
    day = now - dt.timedelta(days=1)
    jobs = {s: db.scalar(select(func.count()).select_from(Job).where(Job.state == s)) or 0 for s in ("running", "queued")}
    jobs["failed_24h"] = db.scalar(select(func.count()).select_from(Job).where(Job.state == "failed",
                                                                                 Job.created_at >= day)) or 0
    alerts = [{"level": a.level, "message": a.message, "started_at": a.started_at.isoformat(),
               "acked": a.acked_at is not None}
              for a in db.scalars(select(MonitorAlert).where(MonitorAlert.cleared_at.is_(None))
                                  .order_by(MonitorAlert.started_at.desc()).limit(100))]
    m = maintenance_mode.get(db)
    return {"name": _name(own_url()), "url": own_url(), "version": version.current(), "at": now.isoformat(),
            "maintenance": {"active": m["active"], "reason": m["reason"]},
            "hosts": hosts, "totals": tot, "environments": envs, "environments_by_status": by_status,
            "jobs": jobs, "alerts": alerts}


# --- polling --------------------------------------------------------------------------------------

def poll(db: Session, link: StackLink, now: dt.datetime | None = None) -> bool:
    now = now or utcnow()
    link.last_try_at = now
    token = get_secret(db, _secret_name(link.id))
    try:
        if not token:
            raise LinkError("no token for this link in the secret store; link the stacks again")
        with client(link.ca_pem) as h:
            r = h.get(f"{link.url}/api/link/summary", headers={"Authorization": f"Bearer {token}"})
        if r.status_code in (401, 403):
            raise LinkError("the other stack no longer accepts this link (removed there?)")
        r.raise_for_status()
        link.summary, link.last_ok_at, link.failures, link.error = r.json(), now, 0, ""
        link.name = (link.summary or {}).get("name") or link.name
        ok = True
    except (httpx.HTTPError, LinkError, ValueError) as exc:
        link.failures = (link.failures or 0) + 1
        link.error = str(exc)[:500] or exc.__class__.__name__
        ok = False
    db.commit()
    return ok


def poll_all(db: Session, now: dt.datetime | None = None) -> None:
    for link in db.scalars(select(StackLink)).all():
        poll(db, link, now)


def stale(link: StackLink, now: dt.datetime | None = None) -> bool:
    if link.last_ok_at is None:
        return True
    t = link.last_ok_at if link.last_ok_at.tzinfo else link.last_ok_at.replace(tzinfo=dt.timezone.utc)
    return (now or utcnow()) - t > STALE_AFTER


def out(link: StackLink, now: dt.datetime | None = None) -> dict:
    return {"id": link.id, "name": link.name, "url": link.url, "ca_fingerprint": link.ca_fingerprint,
            "created_by": link.created_by, "created_at": link.created_at, "last_ok_at": link.last_ok_at,
            "last_try_at": link.last_try_at, "failures": link.failures, "error": link.error,
            "stale": stale(link, now), "summary": link.summary,
            "sign_on": {"enabled": bool(link.sso_in), **(link.sso_state or {})}}
