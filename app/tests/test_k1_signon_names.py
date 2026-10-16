"""Sign-in stays on the name the browser used (design §58)."""
from starlette.requests import Request

from labape.auth import oidc
from labape.config import get_settings


def _request(host: str) -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [(b"host", host.encode())],
                    "query_string": b"", "scheme": "https", "server": ("x", 443)})


def _names(monkeypatch, public="https://172.21.20.11", alt="OZ, oz.jlm.abq,auth.labape.test,OZ"):
    s = get_settings()
    monkeypatch.setattr(s, "public_url", public)
    monkeypatch.setattr(s, "alt_names", alt)


def test_the_stack_has_each_name_once_main_first(monkeypatch):
    _names(monkeypatch)
    assert oidc.stack_names() == ["172.21.20.11", "oz", "oz.jlm.abq", "auth.labape.test"]


def test_a_known_name_is_kept_and_anything_else_falls_back_to_the_main_name(monkeypatch):
    _names(monkeypatch)
    assert oidc.request_name(_request("Auth.LABAPE.test")) == "auth.labape.test"
    assert oidc.request_name(_request("oz:443")) == "oz"
    assert oidc.request_name(_request("evil.example")) == "172.21.20.11"          # never an arbitrary Host header
    assert oidc.request_name(_request("")) == "172.21.20.11"


def test_the_redirect_address_follows_the_name(monkeypatch):
    _names(monkeypatch, public="https://172.21.20.11:8443")
    assert oidc.public_base(_request("oz.jlm.abq")) == "https://oz.jlm.abq:8443"
    assert oidc.public_base(_request("evil.example")) == "https://172.21.20.11:8443"


def test_authentiks_address_follows_the_name_and_keeps_its_port():
    url = "https://172.21.20.11:9445/application/o/authorize/?client_id=labape&state=x"
    assert oidc._on_name(url, "auth.labape.test") == "https://auth.labape.test:9445/application/o/authorize/?client_id=labape&state=x"
    assert oidc._on_name(url, "172.21.20.11") == url
    assert oidc._on_name(url, "") == url
