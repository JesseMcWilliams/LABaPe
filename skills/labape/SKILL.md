---
name: labape
description: Work with a LABaPe stack (lab environments of Windows and Linux VMs on KVM hosts, and its platform CA) through its MCP server or REST API. Use when asked to deploy, inspect, run commands on or destroy a lab environment or its VMs, follow a job, or request, list or revoke a certificate from the lab's CA.
---

# Working with LABaPe

LABaPe builds ephemeral lab environments (VMs on KVM hosts, optionally with an Active Directory domain) and runs the lab's certificate authority. You reach it with the **LABaPe MCP server** (tools named below) or, without it, the REST API with `Authorization: Bearer lpat_...` (`/api/docs` on the stack lists every call). Everything you do is audited and limited by the token's roles, scope and rate limits. Reference: `Claude_Docs/Reference_Console-API-And-MCP.md` in the LABaPe repository.

## What your token allows

The tools you see depend on the token's roles; a tool you can't see is one the token can't use. Don't try to work around a refusal (403, "not offered"): tell the user which role is missing.

| Role | Gives |
|---|---|
| `console` | `console_targets`, `run_command`, `open_session`, `session_input`, `session_output`, `close_session`, `list_sessions` |
| `viewer`, `deployer`, `template_editor`, `file_manager`, `auditor` | read tools: `list_environments`, `get_environment`, `list_vms`, `get_vm`, `list_hosts`, `get_host`, `list_jobs`, `get_job`, `latest_plan` |
| `deployer`, `template_editor` | `deploy_environment`, `plan_redeploy`, `refresh_addresses`, `destroy_environment`, `cancel_job` |
| `ca_requester` | `request_certificate`, `list_certificates`, `get_certificate` |
| `ca_auditor` | `list_certificates` (all), `get_certificate`, `ca_info`; changes nothing |
| `ca_admin` | all of the above plus `revoke_certificate` |

## Rules

1. **Look before you change.** Start with `list_environments` or `console_targets` to get environment ids and VM names; never guess an id.
2. **Ask before anything destructive.** `destroy_environment` deletes every VM and disk and can't be undone; `cancel_job` abandons work; `revoke_certificate` can't be reversed. Say what will be lost and wait for the user's yes.
3. **Preview a redeploy of an existing environment** with `plan_redeploy`, then read it with `latest_plan` (it names the VMs a redeploy would reinstall and why). A redeploy keeps what is inside a VM unless its install recipe changed. Deploying an environment that is a draft (`status: new`) is a first deploy and needs no preview.
4. **Jobs are asynchronous.** Deploy, destroy, plan and refresh return a job id: follow it with `get_job` (state `queued`, `running`, `succeeded`, `failed`, `cancelled`) until it finishes, then report the outcome. One job runs per environment at a time; a second request gets 409. A deploy can take minutes (templates) to an hour or more (Windows from ISO).
5. **Run commands as a named account.** Tokens normally must give `account` (an environment account from its Accounts panel); without one LABaPe would use its own root-level access, which tokens usually aren't allowed to. Linux commands are shell, Windows commands PowerShell.
6. **Sessions:** `open_session` (kind `shell` or `serial`), then `session_input` (end lines with a newline; `\u0003` is Ctrl-C) and `session_output` passing the returned `next` as `after` (wait up to 30 s). One session per target per token; always `close_session` when done.
7. **Output can contain secrets** the console prints. Don't paste credentials into answers or files; the credentials sheet and passwords are not for you to read out.
8. **Maintenance mode** refuses changes (503) while an admin or an update has the stack in maintenance. Reads still work. Say so and wait; don't retry in a loop.

## Certificates

- `request_certificate` gives `{common_name, dns[], ips[]}` and LABaPe makes a P-256 key pair: **the private key is in the result once and LABaPe does not keep it**. Save it where the user said (never in a repository or log) before you do anything else. Or pass a PEM `csr` and only the certificate comes back.
- Names must be under the stack's own names and the domains a CA administrator allowed, no wildcards, and addresses inside the allowed networks (a `ca_admin` token is not limited). A refusal lists what is allowed.
- Lifetime is 30 days by default, at most 90. `environment_id` ties the certificate to an environment so its owners see it.
- `uses` asks for extra attributes (`client_auth`, `code_signing`, `email_protection`, `time_stamping`, `ocsp_signing`, `smartcard_logon`, `signing`, `encryption`). It works only if a CA administrator turned extra attributes on; otherwise the request is refused. A certificate that comes back without what was asked is revoked and refused. Leave `uses` out for the default (server and client authentication).
- The client must trust the lab's root: the root certificate is in the request result (`root`) and at `/api/ca/root.crt`.
- **Revocation is passive:** the CA refuses to renew a revoked certificate and answers a check, but a client that never checks keeps trusting it until it expires. Say so when revoking.

## Things that surprise people

- An environment with `status: new` is a **draft** (saved, not deployed; no quota, addresses or VM names used). Deploy it from its page or `deploy_environment`.
- VM names must be unique across the KVM host; Windows names are at most 15 characters.
- A VM whose guest agent doesn't answer shows "unknown" for load and disk: it may be off or still booting. `list_vms` shows state and addresses; DHCP addresses are found after boot (`refresh_addresses` re-reads them).
- A local account can be limited to chosen VMs of its roles; a host group can be **standalone** (not joined to the domain).
- Don't assume Hyper-V: LABaPe manages libvirt/KVM hosts.

## When something fails

Read the failed job's state and its log through the web interface (the job page) or tell the user the job id; the tools return the job's state and details, not its full log. Report the first error, not a guess at the cause, and ask before retrying a deploy (a retry resumes from OpenTofu's saved state).
