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


# --- elevated accounts: checked on a schedule, restored with akadmin (design §54.1) ---------------------

from contextlib import contextmanager  # noqa: E402

from labape import elevated  # noqa: E402


def _elevated_policy(db, value="rotator-key"):
    appsettings.put(db, "directory", appsettings.DirectorySettings(enabled=True, api_url="https://ak.example.test"))
    put_secret(db, rotation.ELEVATED_PREFIX + "authentik-rotator", value)
    rotation.ensure_policies(db)
    return db.query(SecretPolicy).filter_by(name="elevated.authentik-rotator").one()


def test_every_elevated_account_has_a_policy_that_is_checked_weekly(client):
    _admin_session(client)
    with session_factory()() as db:
        p = _elevated_policy(db)
        assert p.kind == "elevated_account" and p.params == {"account": "authentik-rotator"}
        assert rotation.policy_secret(p) == "elevated/authentik-rotator"
        d = H.describe(p)
        assert d["checkable"] and d["recoverable"] and d["check_every_hours"] == 7 * 24
    assert client.post("/api/admin/secrets/elevated", json={"name": "other-token", "value": "ThisIsMy_FAKE_Password6!"}).status_code == 201
    kinds = {x["label"] for x in client.get("/api/admin/secrets").json()["policies"] if x["kind"] == "elevated_account"}
    assert kinds == {"Elevated account: authentik-rotator", "Elevated account: other-token"}


def test_the_elevated_check(client, monkeypatch):
    _admin_session(client)
    with session_factory()() as db:
        p = _elevated_policy(db)
        seen = []

        def get(url, headers, timeout, verify):
            seen.append((url, headers["Authorization"]))
            return httpx.Response(200, json={}, request=httpx.Request("GET", url))
        monkeypatch.setattr(elevated.httpx, "get", get)
        assert "accepts" in H._elevated_account(db, p) and seen == [("https://ak.example.test/api/v3/core/users/me/", "Bearer rotator-key")]
        monkeypatch.setattr(elevated.httpx, "get", lambda url, headers, timeout, verify: httpx.Response(
            403, request=httpx.Request("GET", url)))
        with pytest.raises(H.CheckError):
            H._elevated_account(db, p)

        def down(*a, **k):
            raise httpx.ConnectError("no route")
        monkeypatch.setattr(elevated.httpx, "get", down)
        with pytest.raises(H.NotInUse):
            H._elevated_account(db, p)
        other = SecretPolicy(name="elevated.vault-admin", kind="elevated_account", label="x", params={"account": "vault-admin"},
                             interval_hours=168)
        with pytest.raises(H.NotInUse):
            H._elevated_account(db, other)                                  # not an Authentik token


def test_akadmin_restores_an_elevated_token_that_fell_out_of_step(client, monkeypatch):
    _admin_session(client)
    posts = []

    class Admin:
        def post(self, url, json):
            posts.append((url, json["key"]))
            return httpx.Response(204, request=httpx.Request("POST", url))

    @contextmanager
    def admin_client(base, password, verify=True):
        assert password == "akadmin-pw"
        yield Admin()
    monkeypatch.setattr(aa, "admin_client", admin_client)
    state = {"key": None}

    def get(url, headers, timeout, verify):
        ok = headers["Authorization"] == f"Bearer {state['key']}"
        return httpx.Response(200 if ok else 401, json={}, request=httpx.Request("GET", url))
    monkeypatch.setattr(elevated.httpx, "get", get)
    with session_factory()() as db:
        p = _elevated_policy(db, "stale-key")
        put_secret(db, aa.SECRET, "akadmin-pw")
        import io
        log = io.StringIO()
        monkeypatch.setitem(H.HEALTH, "elevated_account", H.HEALTH["elevated_account"])
        # the stale key is refused; after akadmin sets a new key Authentik accepts exactly that one
        orig_post = Admin.post

        def post(self, url, json):
            state["key"] = json["key"]
            return orig_post(self, url, json)
        monkeypatch.setattr(Admin, "post", post)
        status = H.run_check(db, p, log, "system")
        assert status == "recovered", log.getvalue()
        new = get_secret(db, rotation.ELEVATED_PREFIX + "authentik-rotator")
    assert new != "stale-key" and posts == [("https://ak.example.test/api/v3/core/tokens/labape-rotator/set_key/", new)]


def test_the_restore_needs_akadmins_password_and_a_known_token(client, monkeypatch):
    _admin_session(client)
    with session_factory()() as db:
        _elevated_policy(db)
        with pytest.raises(elevated.ElevatedError, match="akadmin's password isn't stored"):
            elevated.restore(db, "authentik-rotator")
        put_secret(db, aa.SECRET, "wrong")

        @contextmanager
        def refused(base, password, verify=True):
            raise aa.SignInRefused("no")
            yield
        monkeypatch.setattr(aa, "admin_client", refused)
        with pytest.raises(elevated.ElevatedError, match="doesn't sign in"):
            elevated.restore(db, "authentik-rotator")
        with pytest.raises(elevated.ElevatedError, match="can't restore"):
            elevated.restore(db, "vault-admin")


def test_the_import_keeps_a_stored_password(client, monkeypatch, capsys):
    """labape authentik-admin-import --keep-existing never replaces a stored (perhaps rotated) password (§54.3)."""
    import io

    from labape import authentik_admin, cli
    from labape.db import session_factory
    from labape.security import get_secret

    def run(value, *flags):
        monkeypatch.setattr("sys.stdin", io.StringIO(value + "\n"))
        return cli.main(["authentik-admin-import", *flags])

    assert run("ThisIsMy_FAKE_Password6!", "--keep-existing") == 0            # nothing stored yet: stored
    assert run("ThisIsMy_FAKE_Password6!", "--keep-existing") == 0            # the same one: nothing to do
    assert "already in the secret store" in capsys.readouterr().out
    assert run("Another_FAKE_Password7!", "--keep-existing") == 3             # a different one: left alone
    with session_factory()() as db:
        assert get_secret(db, authentik_admin.SECRET) == "ThisIsMy_FAKE_Password6!"
    assert run("Another_FAKE_Password7!") == 0                                # without the flag it replaces
    with session_factory()() as db:
        assert get_secret(db, authentik_admin.SECRET) == "Another_FAKE_Password7!"
