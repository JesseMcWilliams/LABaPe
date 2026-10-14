"""The logo (design §44, question 68): PNG, JPEG or WebP only, public, removable."""
from test_api import _admin_session

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_logo_upload_serve_and_remove(client):
    _admin_session(client)
    assert client.get("/api/branding").json()["logo"] is None
    assert client.get("/api/branding/logo").status_code == 404
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert client.put("/api/admin/settings/branding/logo", content=svg).status_code == 422
    assert client.put("/api/admin/settings/branding/logo", content=b"\x89PNG" + b"x" * 300_000).status_code == 413
    v = client.put("/api/admin/settings/branding/logo", content=PNG).json()["logo"]
    assert client.get("/api/branding").json()["logo"] == v
    r = client.get("/api/branding/logo")
    assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff" and "sandbox" in r.headers["content-security-policy"]
    webp = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 16
    assert client.put("/api/admin/settings/branding/logo", content=webp).status_code == 200
    assert client.get("/api/branding/logo").headers["content-type"] == "image/webp"
    assert client.delete("/api/admin/settings/branding/logo").json() == {"logo": None}
    assert client.get("/api/branding").json()["logo"] is None
