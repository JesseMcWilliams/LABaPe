"""The API and worker run as the host's service user (plan: Planning_Root-Image.md): the entrypoint, the image,
the compose file, setup.sh and the update agent agree on the LABAPE_UID, LABAPE_GID and LABAPE_EXTRA_GIDS keys."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTAINER = ROOT / "container"


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_the_image_starts_through_the_entrypoint_and_keeps_pings_capability():
    docker = text(CONTAINER / "Dockerfile")
    assert 'ENTRYPOINT ["/opt/labape/entrypoint.sh"]' in docker
    assert "COPY container/entrypoint.sh /opt/labape/entrypoint.sh" in docker
    assert "libcap2-bin" in docker and "setcap cap_net_raw+ep" in docker
    assert docker.index("ENTRYPOINT") < docker.index('CMD ["labape", "serve"]')


def test_the_entrypoint_is_lf_and_drops_to_the_service_user():
    raw = (CONTAINER / "entrypoint.sh").read_bytes()
    assert b"\r" not in raw
    sh = raw.decode()
    assert sh.startswith("#!/bin/bash")
    for needle in ("setpriv --reuid", "--groups", "/etc/passwd", "/etc/group", ".labape-owner", "chown -R -h", "LABAPE_EXTRA_GIDS"):
        assert needle in sh, needle


def test_the_compose_file_passes_the_ids_to_the_api_and_worker_and_guacd_follows_them():
    compose = text(CONTAINER / "compose.yaml")
    for key in ("LABAPE_UID: ${LABAPE_UID:-}", "LABAPE_GID: ${LABAPE_GID:-}", "LABAPE_EXTRA_GIDS: ${LABAPE_EXTRA_GIDS:-}"):
        assert key in compose
    # the shared environment is merged into both services: a service's own `environment:` would replace it
    worker = compose.split("  labape-worker:")[1].split("\n  caddy:")[0]
    assert "\n    environment:" not in worker
    assert 'user: "${LABAPE_UID:-1000}:${LABAPE_GID:-1000}"' in compose.split("  labape-guacd:")[1].split("\n\n")[0]


def test_setup_and_the_update_agent_write_the_same_keys_once():
    for path in (CONTAINER / "setup.sh", ROOT / "deploy" / "update-agent" / "labape-update.sh"):
        sh = text(path)
        assert "LABAPE_UID=%s" in sh and "LABAPE_GID=%s" in sh and "LABAPE_EXTRA_GIDS=%s" in sh, path
        assert "grep -q '^LABAPE_UID=.'" in sh, path
        assert "getent group kvm libvirt || true" in sh, path           # a missing group must not stop a `set -e` script
    assert "user_ids; (cd" in text(ROOT / "deploy" / "update-agent" / "labape-update.sh")


@pytest.mark.skipif(shutil.which("bash") is None or sys.platform == "win32", reason="needs a POSIX bash (on Windows `bash` is WSL)")
def test_without_a_service_user_the_entrypoint_just_runs_the_command(tmp_path):
    for env in ({}, {"LABAPE_UID": ""}, {"LABAPE_UID": "0"}):
        r = subprocess.run(["bash", str(CONTAINER / "entrypoint.sh"), "echo", "ran"], capture_output=True, text=True,
                           env={"PATH": "/usr/bin:/bin:/mingw64/bin", **env}, cwd=tmp_path)
        assert r.returncode == 0 and r.stdout.strip() == "ran", (env, r.stderr)
