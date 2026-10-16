"""An environment's time zone (design §52)."""
import json
from pathlib import Path

from labape import timezones

from test_api import ENV, _admin_session, _host


def test_zone_table_maps_every_name_to_a_windows_name():
    assert timezones.windows_name("America/Los_Angeles") == "Pacific Standard Time"
    assert timezones.windows_name("UTC") == "UTC"
    assert timezones.windows_name("Mars/Olympus") == "UTC"        # unknown: UTC
    names = timezones.names()
    assert names[0] == "UTC" and "Etc/UTC" not in names and len(names) == len(set(names))
    table = json.loads((Path(__file__).resolve().parents[2] / "tofu" / "timezones.json").read_text(encoding="utf-8"))
    assert all(v for v in table.values())


def test_environment_keeps_the_zone_and_defaults_to_utc(client):
    _admin_session(client)
    host_id = _host(client)
    r = client.post("/api/environments", json={**ENV, "kvm_host_id": host_id, "timezone": "America/Denver"})
    assert r.status_code == 201, r.text
    assert r.json()["environment"]["spec"]["timezone"] == "America/Denver"
    r = client.post("/api/environments", json={**ENV, "name": "tz-default", "kvm_host_id": host_id,
                                                  "host_groups": [{**ENV["host_groups"][0], "name": "other"}]})
    assert r.status_code == 201, r.text
    assert r.json()["environment"]["spec"]["timezone"] == "UTC"


def test_unknown_zone_is_refused(client):
    _admin_session(client)
    host_id = _host(client)
    r = client.post("/api/environments", json={**ENV, "kvm_host_id": host_id, "timezone": "Mars/Olympus"})
    assert r.status_code == 422


def test_the_form_lists_the_zones(client):
    _admin_session(client)
    body = client.get("/api/environments/timezones").json()
    assert body["default"] == "UTC" and "Europe/London" in body["timezones"]
