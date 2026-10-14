#!/usr/bin/env python3
"""Deploy LABaPe on a Debian KVM host from scratch, as named, resumable steps.

Follows the BlueTrack installer's model (Deploy/Install-BlueTrack.ps1):
named steps grouped into phases; every answer saved to a reusable answers
file; per-step progress so a failed run can be resumed or single steps
re-run; prompts only for what the selected steps need; a dry run that
changes nothing. See deploy/README.md.

    sudo deploy/install-labape.py                        # full install, prompting as needed
    sudo deploy/install-labape.py --profile kvm          # KVM host only (driven by a web interface elsewhere)
    sudo deploy/install-labape.py --profile web          # web interface only, driving a remote KVM host
    sudo deploy/install-labape.py --config-file deploy/answers.lab.json
    sudo deploy/install-labape.py --list-steps
    sudo deploy/install-labape.py --resume               # carry on after a failure
    sudo deploy/install-labape.py --step 'Stack.*'       # (re)run some steps
    sudo deploy/install-labape.py --start-at Stack.Setup
    sudo deploy/install-labape.py --dry-run              # show what would happen
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import os
import sys
from pathlib import Path

DEPLOY_ROOT = Path(__file__).resolve().parent
REPO = DEPLOY_ROOT.parent
sys.path.insert(0, str(DEPLOY_ROOT))

from labape_deploy import state  # noqa: E402
from labape_deploy import steps as S  # noqa: E402
from labape_deploy.answers import ANSWERS, Resolver  # noqa: E402
from labape_deploy.util import Runner, StepError  # noqa: E402


def cli(a: Resolver) -> bool:
    return S.kvm_side(a) and bool(a.need("InstallCli"))


def kvm(a: Resolver) -> bool:
    return S.kvm_side(a)


def kvm_only(a: Resolver) -> bool:
    return a.need("Profile") == "kvm"


def full(a: Resolver) -> bool:
    return a.need("Profile") == "full" and web(a)


def web(a: Resolver) -> bool:
    return a.need("Profile") != "kvm" and bool(a.need("InstallWebUi"))


def cli_config(a: Resolver) -> bool:
    """The vault, environment.yml and manifests: the web stack copies them; a KVM host the web
    interface drives needs them only for a usable command line there (question 126)."""
    return a.need("Profile") != "kvm" or bool(a.need("CliCheckout"))


def agent(a: Resolver) -> bool:
    return web(a) and bool(a.need("InstallUpdateAgent"))


# name, function, answers it needs, condition (or None), description
STEPS = [
    ("Prereq.Os", S.prereq_os, ["Profile"], None, "Root, Debian, /dev/kvm (KVM profiles)"),
    ("Prereq.User", S.prereq_user, ["ServiceUser"], None, "Service user that owns the repo, vault and SSH keys"),
    ("Prereq.Packages", S.prereq_packages, [], None, "apt packages: QEMU/KVM, libvirt, virt-install, xorriso, Python, smartmontools, lm-sensors"),
    ("Prereq.Libvirt", S.prereq_libvirt, ["ServiceUser"], kvm, "libvirtd running; service user in libvirt and kvm"),
    ("Prereq.NoSleep", S.prereq_no_sleep, [], None, "Never sleep: mask the sleep targets, logind ignores idle and the suspend key"),
    ("Net.Validate", S.network_validate, ["Networks", "DefaultNetwork"], None, "Check the VM network answers"),
    ("Net.BridgeNetfilter", S.net_bridge_netfilter, [], full, "Don't filter bridged VM traffic with iptables (Docker)"),
    ("Net.Bridges", S.net_bridges, ["Networks"], kvm, "Create missing VM bridges on their NICs (NetworkManager)"),
    ("Storage.Dirs", S.storage_dirs, ["VmStoragePath", "TemplateStoragePath", "IsoPath"], kvm,
     "VM, template and ISO directories"),
    ("Storage.TemplatePool", S.storage_template_pool, ["TemplateStoragePath"], kvm,
     "libvirt storage pool labape-templates"),
    ("Tools.Ansible", S.tools_ansible, ["AnsibleCoreVersion", "ServiceUser"], None,
     "ansible-core, pywinrm and the collections (needed for the vault in every install)"),
    ("Tools.OpenTofu", S.tools_opentofu, ["TofuVersion", "InstallCli"], cli, "OpenTofu (checksum-verified)"),
    ("Tools.Packer", S.tools_packer, ["PackerVersion", "InstallCli", "ServiceUser"], cli,
     "Packer and its QEMU/Ansible plugins"),
    ("Config.Secrets", S.config_secrets, ["ServiceUser"], cli_config,
     "Vault password, SSH key pair, secrets.vault.yml with generated passwords"),
    ("Config.Environment", S.config_environment,
     ["DomainName", "NetbiosName", "ManagementSource", "Networks", "DefaultNetwork", "VmStoragePath",
      "TemplateStoragePath", "IsoPath", "ServiceUser"], cli_config, "tofu/environment.yml with the network catalog"),
    ("Config.Manifests", S.config_manifests, ["ServiceUser"], cli_config, "Software and directory manifests from the examples"),
    ("Remote.AuthorizeWorker", S.remote_authorize_worker, ["WorkerPublicKey", "ServiceUser"], kvm_only,
     "Authorize the web interface's worker key for SSH (kvm profile)"),
    ("Stack.Runtime", S.stack_runtime, ["InstallWebUi", "ContainerRuntime", "ServiceUser"], web,
     "Docker (or Podman) and compose"),
    ("Stack.Setup", S.stack_setup, ["InstallWebUi", "WebHostname", "Tls", "VmStoragePath", "ServiceUser"], web,
     "container/.env, generated secrets, config and engine secrets"),
    ("Stack.Services", S.stack_services, ["InstallWebUi", "InstallConjur", "InstallSyslog", "InstallRemoteAccess"], web,
     "Which optional services run (compose.override.yaml) and where the syslog collector listens"),
    ("Stack.Auth", S.stack_auth, ["InstallWebUi", "InstallAuthentik", "WebHostname"], web,
     "Sign-in: Authentik installed with LABaPe, an existing OIDC provider, a throwaway Authentik, or none"),
    ("Stack.Build", S.stack_build, ["InstallWebUi", "ContainerRuntime"], web, "Build the LABaPe image"),
    ("Stack.Up", S.stack_up, ["InstallWebUi", "ContainerRuntime", "WebHostname"], web, "Start the stack"),
    ("Stack.WebTls", S.stack_web_tls, ["InstallWebUi", "Tls", "ServiceUser"], web,
     "The web certificate with every name of the stack (host name, FQDN, address) from the platform CA"),
    ("Stack.Stores", S.stack_stores, ["InstallWebUi", "ContainerRuntime", "ServiceUser", "InstallConjur"], web,
     "OpenBao (and Conjur if chosen) in the stack: initialize, unseal at boot, connect LABaPe (design §26)"),
    ("Stack.UpdateAgent", S.update_agent, ["InstallWebUi", "ServiceUser", "InstallUpdateAgent"], agent,
     "Host-side update agent (user systemd unit, lingering) for updates from the web interface"),
    ("Stack.Restore", S.stack_restore, ["InstallWebUi", "ServiceUser"], web,
     "--from-backup only: put the backup's state onto this stack (deploy/stack-restore.sh)"),
    ("Stack.Bootstrap", S.stack_bootstrap,
     ["InstallWebUi", "KvmHostName", "Networks", "DefaultNetwork", "VmStoragePath", "TemplateStoragePath",
      "AdminGroups", "ContainerRuntime", "IsoPath"], web, "Register this KVM host and its networks in the web interface"),
    ("Templates.Build", S.templates_build, ["TemplatesToBuild", "TemplateStoragePath", "ServiceUser"], kvm,
     "Build the requested templates with Packer (long)"),
    ("Smoke.Cli", S.smoke_cli, ["ServiceUser"], kvm, "libvirt, tofu, ansible and environment.yml as the service user"),
    ("Smoke.Web", S.smoke_web, ["InstallWebUi", "WebHostname", "ContainerRuntime"], web,
     "Web interface health and sign-in providers"),
]
STEP_NAMES = [s[0] for s in STEPS]

# Extra answers a step needs only when another answer has a given value.
CONDITIONAL_NEEDS = {
    "Stack.Services": lambda a: ["SyslogBind"] if a.need("InstallSyslog") else [],
    "Stack.Stores": lambda a: ["PlatformStore"] if a.need("InstallConjur") else [],
    "Stack.Auth": lambda a: [] if a.need("InstallAuthentik") else (
        ["AuthMode"] + (["OidcIssuer", "OidcClientId"] if a.need("AuthMode") == "existing" else [])),
    "Stack.Bootstrap": lambda a: ["RemoteKvmUri"] if a.need("Profile") == "web" else [],
}


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config-file", help="answer file (JSON) for unattended runs; see deploy/answers.sample.json")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                    help="set one answer (JSON values allowed, e.g. InstallCli=false); wins over every file")
    ap.add_argument("--profile", choices=("full", "kvm", "web"),
                    help="what to install: full (default), kvm (KVM host only) or web (web interface only)")
    ap.add_argument("--instance", help="installation name (state files are per name); default: the only saved one, else labape")
    sel = ap.add_mutually_exclusive_group()
    sel.add_argument("--step", action="append", help="run only these steps (wildcards allowed), whatever their status")
    sel.add_argument("--start-at", help="run this step and every step after it")
    sel.add_argument("--resume", action="store_true", help="run every step not yet Completed/NotApplicable")
    ap.add_argument("--list-steps", action="store_true", help="list the steps and their saved status, then exit")
    ap.add_argument("--dry-run", action="store_true", help="show what would be done without changing anything")
    ap.add_argument("--force", action="store_true", help="redo work a step would skip (rewrite environment.yml, reinstall tools)")
    ap.add_argument("--non-interactive", action="store_true", help="never prompt; fail if an answer is missing")
    ap.add_argument("--fresh", action="store_true", help="ignore the saved answers file and ask again")
    ap.add_argument("--from-backup", metavar="ARCHIVE",
                    help="install the stack a full-stack backup (deploy/stack-backup.sh) was taken of: its answers, "
                         "then its state (Stack.Restore). Templates aren't built (TemplatesToBuild=[])")
    ap.add_argument("--backup-key-file", metavar="FILE", help="the backup key, with --from-backup")
    ap.add_argument("--restore-drill", action="store_true",
                    help="with --from-backup: leave maintenance mode on, so the copy runs no jobs against real hosts")
    return ap.parse_args(argv)


def parse_sets(items: list[str]) -> dict:
    out = {}
    for item in items:
        name, sep, raw = item.partition("=")
        if not sep or name not in ANSWERS:
            raise SystemExit(f"error: --set {item!r}: expected NAME=VALUE with NAME one of {', '.join(ANSWERS)}")
        try:
            out[name] = json.loads(raw)
        except json.JSONDecodeError:
            out[name] = raw
    return out


def pick_instance(args, from_files: dict) -> str:
    if args.instance:
        return args.instance
    if "InstanceName" in from_files:
        return from_files["InstanceName"]
    saved = sorted(state.state_dir(DEPLOY_ROOT).glob("answers.*.json"))
    names = [p.name[len("answers."):-len(".json")] for p in saved]
    if len(names) == 1:
        return names[0]
    if len(names) > 1 and (args.resume or args.step or args.start_at or args.list_steps):
        raise SystemExit(f"error: saved state for several instances ({', '.join(names)}); pass --instance")
    return ANSWERS["InstanceName"].default


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            s.write(data)
            s.flush()

    def flush(self):
        for s in self.streams:
            s.flush()


def summary(a: Resolver) -> str:
    """Every answer this run uses (none is a secret), the new ones marked."""
    lines = ["Answers (* new this run):"]
    width = max((len(n) for n in a.answers), default=0)
    for n in ANSWERS:
        if n in a.answers and ANSWERS[n].ask:
            v = a.answers[n]
            if isinstance(v, list) and v and isinstance(v[0], dict):
                v = ", ".join(x.get("Name", "?") for x in v)
            lines.append(f"  {'*' if n in a.prompted else ' '} {n:<{width}}  {v}")
    return "\n".join(lines)


def main(argv=None) -> int:
    args = parse_args(argv)
    a = Resolver(interactive=sys.stdin.isatty() and not args.non_interactive)

    # Precedence: --set, then --config-file, then a backup's answers, then saved answers (partial runs).
    a.add(parse_sets(args.set))
    if args.profile:
        a.add({"Profile": args.profile})
    from_config = state.read_answer_file(Path(args.config_file)) if args.config_file else {}
    a.add(from_config)
    if args.from_backup:
        if not args.backup_key_file:
            raise SystemExit("error: --from-backup needs --backup-key-file")
        from labape_deploy import restore
        try:
            from_backup, manifest = restore.archive_answers(REPO, Path(args.from_backup), Path(args.backup_key_file))
        except StepError as exc:
            raise SystemExit(f"error: {exc}") from exc
        print(f"Installing from {manifest.get('name', args.from_backup)} (taken {manifest.get('created_at', '?')} "
              f"on {manifest.get('host', '?')}, LABaPe {manifest.get('version', '?')})")
        if not from_backup:
            print("The backup has no installer answers (that stack wasn't installed with this installer): "
                  "give them with --config-file/--set or at the prompts.")
        a.add({"TemplatesToBuild": []})            # templates aren't in a backup; build them later
        a.add(from_backup)
        from_config = {**from_backup, **from_config}
    instance = pick_instance(args, {**from_config, **a.answers})
    a.add({"InstanceName": instance})
    answers_path = state.state_path(DEPLOY_ROOT, instance, "answers")
    progress_path = state.state_path(DEPLOY_ROOT, instance, "progress")
    # Every run reuses the saved answers (question 141): an update or a re-run asks only the
    # questions added since. --fresh asks everything again.
    if answers_path.is_file() and not args.fresh:
        print(f"Using saved answers from {answers_path} (--fresh to answer again)")
        a.add(state.read_answer_file(answers_path))
    progress = state.read_progress(progress_path)

    if args.list_steps:
        width = max(len(n) for n in STEP_NAMES)
        for name, _, _, _, desc in STEPS:
            p = progress.get(name, {})
            print(f"{name:<{width}}  {p.get('status', ''):<13} {p.get('time', ''):<19}  {desc}"
                  + (f"\n{'':<{width}}  {p['message']}" if p.get("message") else ""))
        print(f"\nSaved state: {progress_path}")
        return 0

    # Select steps.
    if args.step:
        selected = [s for s in STEPS if any(fnmatch.fnmatch(s[0], pat) for pat in args.step)]
        unmatched = [pat for pat in args.step if not any(fnmatch.fnmatch(n, pat) for n in STEP_NAMES)]
        if unmatched:
            raise SystemExit(f"error: unknown step(s): {', '.join(unmatched)} (see --list-steps)")
    elif args.start_at:
        if args.start_at not in STEP_NAMES:
            raise SystemExit(f"error: unknown step {args.start_at!r} (see --list-steps)")
        selected = STEPS[STEP_NAMES.index(args.start_at):]
    elif args.resume:
        if not progress:
            raise SystemExit(f"error: nothing to resume: no saved progress at {progress_path}")
        selected = [s for s in STEPS if progress.get(s[0], {}).get("status") not in ("Completed", "NotApplicable")]
        if not selected:
            print("Every step has already completed. Use --step or --start-at to re-run steps.")
            return 0
    else:
        selected = STEPS
        progress = {}
        if progress_path.is_file() and not args.dry_run:
            progress_path.unlink()

    # Log the run.
    log_dir = DEPLOY_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"install-{instance}-{dt.datetime.now():%Y%m%d-%H%M%S}.log"
    log = open(log_path, "w", encoding="utf-8")
    sys.stdout = Tee(sys.__stdout__, log)
    print(f"Logging this run to {log_path}")
    print(f"Steps this run: {', '.join(s[0] for s in selected)}")

    # Resolve every answer the selected steps need (and only those), then save.
    for name, _, needs, cond, _ in selected:
        if cond is not None and "Profile" in a.answered and not cond(a):
            continue  # the step won't run: don't ask for its answers
        for n in needs:
            a.need(n)
        if name in CONDITIONAL_NEEDS and (cond is None or cond(a)):
            for n in CONDITIONAL_NEEDS[name](a):
                a.need(n)
    for n in ANSWERS:  # defaults that are never prompted are saved too, so the file is complete
        if not ANSWERS[n].ask:
            a.need(n)
    if a.prompted and a.interactive and not args.dry_run:
        print("\n" + summary(a))
        if input("\nInstall with these answers? (Y/n) ").strip().lower() not in ("", "y", "yes"):
            print("Nothing changed. Run again to change the answers (the saved ones are kept only after a confirmed run).")
            return 1
    state.save_answer_file(answers_path, a.answers, args.dry_run)
    print(f"Answers saved to {answers_path} (reuse with --config-file, or --resume after a failure).")

    runner = Runner(args.dry_run, a.answers.get("ServiceUser"))
    ctx = S.Ctx(r=runner, a=a, repo=REPO, deploy_root=DEPLOY_ROOT, force=args.force,
                backup=Path(args.from_backup).resolve() if args.from_backup else None,
                backup_key=Path(args.backup_key_file).resolve() if args.backup_key_file else None,
                restore_drill=args.restore_drill)
    results = []
    for name, fn, _, cond, _ in selected:
        print(f"\n=== Step: {name} ===", flush=True)
        if cond is not None and not cond(a):
            status, message = "NotApplicable", "turned off in the answers"
        else:
            try:
                status, message = fn(ctx)
            except (StepError, OSError, ValueError) as exc:
                state.save_step(progress_path, progress, name, "Failed", str(exc), args.dry_run)
                print(f"\nStep {name} failed: {exc}\nFix the cause, then re-run with --resume (or --step {name}).")
                print(f"Full log: {log_path}")
                return 1
        state.save_step(progress_path, progress, name, status, message, args.dry_run)
        results.append((name, status, message))
        print(f"{status}: {message}")

    print("\n=== Summary ===")
    width = max(len(n) for n, _, _ in results) if results else 0
    for name, status, message in results:
        print(f"{name:<{width}}  {status:<13} {message}")
    outstanding = [n for n in STEP_NAMES if progress.get(n, {}).get("status") not in ("Completed", "NotApplicable")]
    if outstanding:
        print(f"\nNot yet completed: {', '.join(outstanding)}. Run with --resume to continue.")
    elif web(a):
        host = a.answers.get("WebHostname")
        print(f"\nWeb interface: https://{host}/")
        print("First admin sign-in (if no Authentik admin exists yet):")
        print(f"  cd {REPO / 'container'} && {' '.join(ctx.compose)} exec labape-api labape breakglass enable")
        print("Group memberships added this run (libvirt, kvm, docker) apply at the service user's next login.")
        if a.answers.get("Profile") == "web":
            print("The KVM host must authorize this web interface's worker key. Print it with:")
            print(f"  cd {REPO / 'container'} && {' '.join(ctx.compose)} exec labape-worker labape worker-key")
            print("and install the KVM host with: sudo deploy/install-labape.py --profile kvm --set WorkerPublicKey='<key>'")
    print(f"Full log: {log_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\ninterrupted; re-run with --resume to continue")
        raise SystemExit(130)
