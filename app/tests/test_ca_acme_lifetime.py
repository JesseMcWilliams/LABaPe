"""The platform CA's ACME provisioner gets a certificate lifetime (found with Home Assistant's Let's Encrypt
add-on: step-ca's built-in default is 24 hours and an ACME client cannot ask for another)."""
from pathlib import Path

INIT = (Path(__file__).resolve().parents[2] / "container" / "ca" / "init.sh").read_bytes().decode()


def test_the_acme_provisioner_gets_90_days_on_every_start_unless_already_set():
    assert "\r" not in INIT
    assert "step ca provisioner update acme --x509-default-dur 2160h --x509-max-dur 2160h" in INIT
    # guarded by what ca.json already says, and outside the first-start block so an existing CA is corrected
    guard = INIT.index('select(.name == "acme") | .claims.defaultTLSCertDuration')
    assert guard > INIT.index("\nfi\n") and guard < INIT.index("exec /usr/local/bin/step-ca")
    assert "step ca provisioner update labape-runner --x509-default-dur 720h --x509-max-dur 2160h" in INIT


def test_a_failed_lifetime_update_warns_and_does_not_stop_the_ca():
    assert "could not set the ACME certificate lifetime" in INIT.split("exec /usr/local/bin/step-ca")[0]


def test_the_ca_keeps_its_database_in_the_stacks_postgres_and_the_backup_covers_it():
    root = Path(__file__).resolve().parents[2]
    assert "database_url" in INIT and '"postgresql"' in INIT and "sslmode=disable" in INIT
    assert INIT.index("database_url") < INIT.index("exec /usr/local/bin/step-ca")
    compose = (root / "container" / "compose.yaml").read_text(encoding="utf-8")
    ca_block = compose.split("  labape-ca:")[1].split("\n  caddy:")[0].split("\n  labape-")[0]
    assert "secrets: [ca_password, database_url]" in ca_block and "condition: service_healthy" in ca_block
    assert "database_url" in (root / "container" / "quadlet" / "labape-ca.container").read_text(encoding="utf-8")
    assert "stepca.dump" in (root / "deploy" / "stack-backup.sh").read_text(encoding="utf-8")
    assert "stepca.dump" in (root / "deploy" / "stack-restore.sh").read_text(encoding="utf-8")
    agent = (root / "deploy" / "update-agent" / "labape-update.sh").read_text(encoding="utf-8")
    # the CA's connection string holds the password: a rotated database password recreates the CA too
    assert agent.count("recreate labape-api labape-worker labape-ca") == 2
