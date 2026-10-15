"""The Authentik administrator's password in the secret store (design §54): import, check, rotate, reveal."""
import httpx
import pytest

from labape import appsettings, authentik_admin as aa
from labape.db import session_factory
from labape.engine import rotation, secret_health as H
from labape.models import SecretPolicy
from labape.security import get_secret, put_secret

from test_api import _admin_session
from test_rotation import _policy

PW = "ThisIsMy_FAKE_Password6!"


def _setup(db):
    appsettings.put(db, "directory", appsettings.DirectorySettings(enabled=True, api_url="https://ak.example.test"))
    put_secret(db, rotation.ELEVATED_PREFIX + "ak-admin", PW)
    rotation.ensure_policies(db)
    p = _policy(db, "authentik_admin")
    p.elevated_ref = "ak-admin"
    db.commit()
    return p


def test_the_policy_exists_and_points_at_the_secret(client):
    _admin_session(client)
    with session_factory()() as db:
        rotation.ensure_policies(db)
        p = _policy(db, "authentik_admin")
        assert rotation.policy_secret(p) == aa.SECRET and p.interval_hours == 720
        assert H.describe(p)["checkable"] and H.describe(p)["recoverable"]
    row = next(x for x in client.get("/api/admin/secrets").json()["policies"] if x["kind"] == "authentik_admin")
    assert row["revealable"] is True and row["needs_elevated"] is True and row["section"] == "stack"


def test_the_check(client, monkeypatch):
    _admin_session(client)
    with session_factory()() as db:
        p = _setup(db)
        with pytest.raises(H.NotInUse):
            H._authentik_admin(db, p)                                      # nothing stored yet
        put_secret(db, aa.SECRET, "stored-pw")
        seen = []
        monkeypatch.setattr(aa, "signs_in", lambda base, user, pw, verify=True: seen.append((base, user, pw)) or True)
        assert "signs in" in H._authentik_admin(db, p) and seen == [("https://ak.example.test", "akadmin", "stored-pw")]
        monkeypatch.setattr(aa, "signs_in", lambda *a, **k: False)
        with pytest.raises(H.CheckError):
            H._authentik_admin(db, p)

        def down(*a, **k):
            raise httpx.ConnectError("no route")
        monkeypatch.setattr(aa, "signs_in", down)
        with pytest.raises(H.NotInUse):
            H._authentik_admin(db, p)


def _fake_authentik(monkeypatch, set_status=204, signs=True):
    calls = []

    def get(url, params, headers, timeout, verify):
        calls.append(("GET", url, params))
        return httpx.Response(200, json={"results": [{"pk": 4, "username": "akadmin"}]}, request=httpx.Request("GET", url))

    def post(url, json, headers, timeout, verify):
        calls.append(("POST", url, json["password"], headers["Authorization"]))
        return httpx.Response(set_status, request=httpx.Request("POST", url))
    monkeypatch.setattr(rotation.httpx, "get", get)
    monkeypatch.setattr(rotation.httpx, "post", post)
    monkeypatch.setattr(aa, "signs_in", lambda *a, **k: signs)
    return calls


def test_rotation_sets_checks_and_stores(client, monkeypatch):
    _admin_session(client)
    calls = _fake_authentik(monkeypatch)
    with session_factory()() as db:
        p = _setup(db)
        put_secret(db, aa.SECRET, "old-pw")
        assert "akadmin has a new password" in rotation.rotate_authentik_admin(db, p, None)
        new = get_secret(db, aa.SECRET)
    assert new != "old-pw" and len(new) >= 32
    assert calls[1] == ("POST", "https://ak.example.test/api/v3/core/users/4/set_password/", new, f"Bearer {PW}")


def test_a_password_that_does_not_sign_in_is_put_back(client, monkeypatch):
    _admin_session(client)
    calls = _fake_authentik(monkeypatch, signs=False)
    with session_factory()() as db:
        p = _setup(db)
        put_secret(db, aa.SECRET, "old-pw")
        with pytest.raises(rotation.RotationError) as exc:
            rotation.rotate_authentik_admin(db, p, None)
        assert "old one was put back" in str(exc.value) and get_secret(db, aa.SECRET) == "old-pw"
    assert [c[2] for c in calls if c[0] == "POST"][-1] == "old-pw"


def test_a_refused_change_keeps_the_old_password(client, monkeypatch):
    _admin_session(client)
    _fake_authentik(monkeypatch, set_status=403)
    with session_factory()() as db:
        p = _setup(db)
        put_secret(db, aa.SECRET, "old-pw")
        with pytest.raises(rotation.RotationError) as exc:
            rotation.rotate_authentik_admin(db, p, None)
        assert "old one stays" in str(exc.value) and get_secret(db, aa.SECRET) == "old-pw"


def test_reveal_is_for_admins_audited_and_only_for_this_secret(client):
    _admin_session(client)
    with session_factory()() as db:
        rotation.ensure_policies(db)
        ak, ca = _policy(db, "authentik_admin").id, _policy(db, "ca_password").id
    assert client.post(f"/api/admin/secrets/{ak}/reveal").status_code == 404          # nothing stored yet
    with session_factory()() as db:
        put_secret(db, aa.SECRET, "stored-pw")
    r = client.post(f"/api/admin/secrets/{ak}/reveal")
    assert r.status_code == 200 and r.json() == {"value": "stored-pw"}
    assert client.post(f"/api/admin/secrets/{ca}/reveal").status_code == 404           # never shown
    audit = client.get("/api/admin/audit?action=secret.reveal").json()
    assert audit and "stored-pw" not in str(audit)


def test_signs_in_follows_the_flow(monkeypatch):
    steps = []

    class Resp:
        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self.body

    class Client:
        cookies = {}

        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return Resp({"component": "ak-stage-identification"})

        def post(self, url, json, headers):
            steps.append(json)
            if json["component"] == "ak-stage-identification":
                return Resp({"component": "ak-stage-password"})
            return Resp({"component": "xak-flow-redirect"} if json["password"] == "right" else {"component": "ak-stage-password"})
    monkeypatch.setattr(aa.httpx, "Client", Client)
    assert aa.signs_in("https://ak.example.test", "akadmin", "right") is True
    assert aa.signs_in("https://ak.example.test", "akadmin", "wrong") is False
    assert steps[0] == {"component": "ak-stage-identification", "uid_field": "akadmin"}
