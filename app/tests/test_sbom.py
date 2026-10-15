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
    for arg, name in (("TOFU_VERSION", "OpenTofu"), ("PACKER_VERSION", "Packer"), ("STEP_VERSION", "step CLI")):
        version = next(line.split("=", 1)[1] for line in dockerfile.splitlines() if line.startswith(f"ARG {arg}="))
        assert (name, version) in have, f"{name} {version} (Dockerfile) isn't in the SBOM: regenerate it"
    compose = (ROOT / "container" / "compose.yaml").read_text(encoding="utf-8")
    for image in ("openbao/openbao:2.7.1", "smallstep/step-ca:0.30.2", "guacamole/guacd:1.6.0", "cyberark/conjur:1.27.0",
                  "postgres:17-alpine", "nginx:1.28-alpine"):
        assert f"docker.io/{image}" in compose or f"docker.io/library/{image}" in compose
        assert any(c["name"] == image for c in bom["components"]), f"{image} isn't in the SBOM"


def _snapshot():
    spec = importlib.util.spec_from_file_location("labape_sbom_snapshot", ROOT / "tools" / "helpers" / "sbom_snapshot.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_compose_image_is_documented():
    """A new image in a compose file must be described in the SBOM's table (tools/helpers/sbom.py IMAGES)."""
    mod = _sbom()
    documented = {i[0] for i in mod.IMAGES}
    found = set()
    for f in (ROOT / "container" / "compose.yaml", ROOT / "tools" / "authentik-test" / "compose.yaml"):
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("image:") and "${LABAPE" not in line:
                image = line.split(":", 1)[1].strip().replace("docker.io/library/", "").replace("docker.io/", "")
                found.add(image.replace("${AUTHENTIK_TAG:-", "").replace("}", ""))
    assert found, "no images found in the compose files"
    assert not found - documented, f"add to IMAGES in tools/helpers/sbom.py: {sorted(found - documented)}"


def test_snapshot_parsers():
    snap = _snapshot()
    assert snap.parse_tools("OpenTofu v1.12.6\non darwin\nPacker v1.16.1\nSmallstep CLI/0.30.2 (linux/amd64)\n") == \
        {"OpenTofu": "1.12.6", "Packer": "1.16.1", "step CLI": "0.30.2"}
    assert snap.parse_collections(json.dumps({"/usr/share/ansible/collections/ansible_collections": {
        "ansible.windows": {"version": "3.8.0"}, "microsoft.ad": {"version": "1.12.1"}}})) == \
        {"ansible.windows": "3.8.0", "microsoft.ad": "1.12.1"}
    assert snap.parse_providers(
        "/opt/labape/tofu-mirror/registry.opentofu.org/dmacvicar/libvirt/terraform-provider-libvirt_0.9.9_linux_amd64.zip\n"
        "/opt/labape/tofu-mirror/registry.opentofu.org/hashicorp/null/terraform-provider-null_3.3.2_linux_amd64.zip") == \
        {"dmacvicar/libvirt": "0.9.9", "hashicorp/null": "3.3.2"}
    assert snap.parse_plugins(
        "/opt/labape/packer-plugins/github.com/hashicorp/qemu/packer-plugin-qemu_v1.1.7_x5.0_linux_amd64\n"
        "/opt/labape/packer-plugins/github.com/hashicorp/ansible/packer-plugin-ansible_v1.1.6_x5.0_linux_amd64") == \
        {"hashicorp/ansible": "1.1.6", "hashicorp/qemu": "1.1.7"}
    assert snap.parse_images("caddy:2 caddy@sha256:abc\nlabape:local \npostgres:17-alpine postgres@sha256:def") == \
        {"caddy:2": "sha256:abc", "postgres:17-alpine": "sha256:def"}          # an image built here has no digest


def test_the_release_script_does_the_sbom():
    release = (ROOT / "tools" / "helpers" / "release.sh").read_text(encoding="utf-8")
    assert "sbom_snapshot.py" in release and "sbom.py" in release and "labape-$VERSION.cdx.json" in release
    assert release.index("git checkout -q -b") < release.index("# 2b.") < release.index("# 3.")   # on the release branch, before the commit
