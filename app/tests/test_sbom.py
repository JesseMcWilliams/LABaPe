"""The software bill of materials (Claude_Docs/Reference_SBOM.md, sbom/labape.cdx.json) matches the
repository: a changed dependency means running `python tools/helpers/sbom.py` and committing the result."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _sbom():
    spec = importlib.util.spec_from_file_location("labape_sbom", ROOT / "tools" / "helpers" / "sbom.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_files_are_current():
    mod = _sbom()
    bom, snap, npm, direct, py, declared, version = mod.build()
    stale = [p.name for p, text in ((mod.MD, mod.document(bom, snap, npm, direct, py, declared, version)),
                                    (mod.CDX, json.dumps(bom, indent=1, ensure_ascii=False) + "\n"))
             if p.read_text(encoding="utf-8") != text]
    assert not stale, f"{', '.join(stale)} out of date: run `python tools/helpers/sbom.py` and commit the result"


def test_the_cyclonedx_file_is_consistent():
    bom = json.loads((ROOT / "sbom" / "labape.cdx.json").read_text(encoding="utf-8"))
    assert bom["bomFormat"] == "CycloneDX" and bom["specVersion"] == "1.5"
    refs = [c["bom-ref"] for c in bom["components"]]
    assert len(refs) == len(set(refs)) and len(refs) > 250
    known = set(refs) | {bom["metadata"]["component"]["bom-ref"]}
    assert all(x in known for d in bom["dependencies"] for x in d.get("dependsOn", []))      # nothing dangling
    assert {d["ref"] for d in bom["dependencies"]} == known                                  # every component listed
    assert all(c["purl"].startswith("pkg:") for c in bom["components"] if "purl" in c)
    version = (ROOT / "web" / "package.json").read_text(encoding="utf-8")
    assert f'"version": "{bom["metadata"]["component"]["version"]}"' in version               # the UI and SBOM agree


def test_the_pins_in_the_dockerfile_are_in_the_sbom():
    dockerfile = (ROOT / "container" / "Dockerfile").read_text(encoding="utf-8")
    bom = json.loads((ROOT / "sbom" / "labape.cdx.json").read_text(encoding="utf-8"))
    have = {(c["name"], c["version"]) for c in bom["components"]}
    for arg, name in (("TOFU_VERSION", "OpenTofu"), ("PACKER_VERSION", "Packer"), ("STEP_VERSION", "step CLI"),
                      ("ANSIBLE_CORE_VERSION", "ansible-core")):
        version = next(line.split("=", 1)[1] for line in dockerfile.splitlines() if line.startswith(f"ARG {arg}="))
        assert (name, version) in have, f"{name} {version} (Dockerfile) isn't in the SBOM: regenerate it"
    compose = (ROOT / "container" / "compose.yaml").read_text(encoding="utf-8")
    for image in ("openbao/openbao:2.7.1", "smallstep/step-ca:0.30.2", "guacamole/guacd:1.6.0", "cyberark/conjur:1.27.0",
                  "postgres:17-alpine", "nginx:1.28-alpine"):
        assert f"docker.io/{image}" in compose or f"docker.io/library/{image}" in compose
        assert any(c["name"] == image for c in bom["components"]), f"{image} isn't in the SBOM"
