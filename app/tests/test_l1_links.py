"""Batch L1 (design §34): linking two stacks with a one-time code, the summary they read, polling."""
import base64
import datetime as dt
import json

import pytest

from labape import ca, links
from labape.db import session_factory
from labape.engine import stackmon
from labape.models import StackLink, StackLinkInvite
from labape.security import get_secret

from test_api import _admin_session, _host


def _pem() -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test CA")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(7).not_valid_before(now).not_valid_after(now + dt.timedelta(days=1)).sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM).decode()


OURS, THEIRS = _pem(), _pem()


@pytest.fixture(autouse=True)
def _ca(monkeypatch):
    monkeypatch.setattr(ca, "root_pem", lambda: OURS)
    monkeypatch.setattr(links, "own_url", lambda: "https://labape.local")      # the test app's is http


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, json.dumps(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError(f"{self.status_code}", request=None, response=None)


class Fake:
    """httpx.Client stand-in: records calls, answers from a table."""
    calls: list = []
    answers: dict = {}

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, json=None, headers=None):
        Fake.calls.append(("POST", url, json, headers))
        return Fake.answers.get(("POST", url.split("/api/")[1]), Resp(200, {}))

    def get(self, url, headers=None):
        Fake.calls.append(("GET", url, None, headers))
        return Fake.answers.get(("GET", url.split("/api/")[1]), Resp(200, {}))


def _code(url, pem, secret="s3cret-code"):
    p = {"u": url, "f": links.fingerprint(pem), "c": secret}
    return links.CODE_PREFIX + base64.urlsafe_b64encode(json.dumps(p).encode()).decode().rstrip("=")


def test_an_invite_is_good_once_for_an_hour(client):
    _admin_session(client)
    with session_factory()() as db:
        inv = links.invite(db, "admin")
        c = links.parse_code(inv["code"])
        assert c["url"] == links.own_url() and c["fingerprint"] == links.fingerprint(OURS)
        out = links.accept(db, c["secret"], "https://b.lab/", THEIRS, "t" * 40)
        b = db.scalar(__import__("sqlalchemy").select(StackLink).where(StackLink.url == "https://b.lab"))
        assert b.name == "b.lab" and get_secret(db, f"link.{b.id}.outbound") == "t" * 40
        assert links.caller(db, f"Bearer {out['token']}").id == b.id
        assert links.caller(db, "Bearer nope") is None and links.caller(db, None) is None
        with pytest.raises(links.LinkError, match="isn't valid"):
            links.accept(db, c["secret"], "https://c.lab", THEIRS, "t" * 40)            # used
        old = links.invite(db, "admin", now=dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2))
        with pytest.raises(links.LinkError, match="isn't valid"):
            links.accept(db, links.parse_code(old["code"])["secret"], "https://c.lab", THEIRS, "t" * 40)
        assert db.query(StackLinkInvite).count() == 2


def test_joining_checks_the_ca_against_the_code(client, monkeypatch):
    _admin_session(client)
    monkeypatch.setattr(links, "client", Fake)
    monkeypatch.setattr(links, "fetch_ca", lambda url: THEIRS)
    Fake.calls, Fake.answers = [], {("POST", "link/join"): Resp(200, {"token": "T" * 43})}
    with session_factory()() as db:
        with pytest.raises(links.LinkError, match="isn't the one in the link code"):
            links.join(db, _code("https://a.lab", _pem()), "admin")
        with pytest.raises(links.LinkError, match="own"):
            links.join(db, _code(links.own_url(), OURS), "admin")
        link = links.join(db, _code("https://a.lab", THEIRS), "admin")
        sent = Fake.calls[-1][2]
        assert sent["code"] == "s3cret-code" and sent["url"] == links.own_url() and sent["ca_pem"] == OURS
        assert links.caller(db, f"Bearer {sent['token']}").id == link.id          # what a.lab will call with
        assert get_secret(db, f"link.{link.id}.outbound") == "T" * 43
        with pytest.raises(links.LinkError, match="already linked"):
            links.join(db, _code("https://a.lab", THEIRS), "admin")
        Fake.answers[("POST", "link/join")] = Resp(400, {"detail": "the link code isn't valid"})
        with pytest.raises(links.LinkError, match="refused: the link code isn't valid"):
            links.join(db, _code("https://z.lab", THEIRS), "admin")


def test_the_peer_api(client):
    _admin_session(client)
    _host(client)
    with session_factory()() as db:
        c = links.parse_code(links.invite(db, "admin")["code"])
        token = links.accept(db, c["secret"], "https://b.lab", THEIRS, "t" * 40)["token"]
    assert client.get("/api/link/summary").status_code == 401
    s = client.get("/api/link/summary", headers={"Authorization": f"Bearer {token}"}).json()
    assert s["hosts"][0]["name"] == "kvm1" and s["url"] == links.own_url() and "maintenance" in s
    assert client.get("/api/link/ca").json()["pem"] == OURS
    m = client.get("/api/admin/links").json()
    assert m["links"][0]["url"] == "https://b.lab" and m["links"][0]["stale"] is True
    bad = client.post("/api/link/join", json={"code": "x" * 12, "url": "https://c.lab", "ca_pem": THEIRS, "token": "t" * 40})
    assert bad.status_code == 400
    assert client.post("/api/link/leave", headers={"Authorization": f"Bearer {token}"}).json()["ok"]
    assert client.get("/api/admin/links").json()["links"] == []


def test_polling_keeps_the_last_summary_and_alerts(client, monkeypatch):
    _admin_session(client)
    monkeypatch.setattr(links, "client", Fake)
    now = dt.datetime(2026, 10, 14, 12, 0, tzinfo=dt.timezone.utc)
    with session_factory()() as db:
        c = links.parse_code(links.invite(db, "admin")["code"])
        links.accept(db, c["secret"], "https://b.lab", THEIRS, "t" * 40)
        link = db.query(StackLink).one()
        Fake.calls, Fake.answers = [], {("GET", "link/summary"): Resp(200, {"name": "pi.lab", "hosts": []})}
        assert links.poll(db, link, now) and link.summary["name"] == "pi.lab" and link.name == "pi.lab"
        assert Fake.calls[-1][3] == {"Authorization": "Bearer " + "t" * 40}
        assert not links.stale(link, now + dt.timedelta(minutes=2)) and links.stale(link, now + dt.timedelta(minutes=5))
        Fake.answers[("GET", "link/summary")] = Resp(401, {"detail": "Not a linked stack"})
        assert not links.poll(db, link, now) and "no longer accepts" in link.error
        assert link.summary["name"] == "pi.lab"                                   # last data kept
        assert not any(k.startswith("link:") for k in stackmon.evaluate(db, now))
        links.poll(db, link, now)
        msgs = {v[2] for k, v in stackmon.evaluate(db, now).items() if k.startswith("link:")}
        assert msgs == {"linked stack pi.lab isn't answering (the other stack no longer accepts this link (removed there?))"}
        links.remove(db, link)
        assert Fake.calls[-1][0:2] == ("POST", "https://b.lab/api/link/leave")
        assert db.query(StackLink).count() == 0 and get_secret(db, f"link.{link.id}.outbound") is None


def test_the_overall_view_adds_up_every_stack(client):
    _admin_session(client)
    _host(client)
    assert client.get("/api/dashboard/stacks").json() == {"stacks": [], "totals": None}
    with session_factory()() as db:
        db.add(StackLink(name="pi.lab", url="https://pi.lab", ca_pem=THEIRS, ca_fingerprint="0" * 64, inbound_hash="x",
                         last_ok_at=dt.datetime.now(dt.timezone.utc), failures=0,
                         summary={"name": "pi.lab", "hosts": [
                             {"name": "garagepc", "machine": "garage-pc", "enabled": True, "cpus": 4, "vms_running": 3,
                              "collected_at": "2026-10-14T12:00:00"},
                             {"name": "kvm1", "machine": "labape.local", "enabled": True, "cpus": 99, "collected_at": None}],
                                  "environments": [{"name": "lab2"}],
                                  "jobs": {"running": 1, "queued": 2}, "alerts": [{"level": "error"}, {"level": "warning"}]}))
        db.add(StackLink(name="old.lab", url="https://old.lab", ca_pem=THEIRS, ca_fingerprint="1" * 64, inbound_hash="y",
                         failures=3, error="timed out"))
        db.commit()
    m = client.get("/api/dashboard/stacks").json()
    assert [s["status"] for s in m["stacks"]] == ["ok", "never", "ok"]       # this stack first, then by name
    t = m["totals"]
    mine = m["stacks"][0]["summary"]["hosts"][0]
    assert mine["machine"] == "labape.local"            # no facts, qemu:///system: this stack's own host
    # kvm1 is in both stacks: once (neither has a newer reading, so the first, this stack's, stays).
    assert t["stacks"] == 3 and t["answering"] == 2 and t["hosts"] == 2 and t["environments"] == 1
    assert t["cpus"] == 4 + (mine["cpus"] or 0) and t["vms_running"] == 3
    assert t["jobs_running"] == 1 and t["jobs_queued"] == 2 and t["alerts_error"] == 1
