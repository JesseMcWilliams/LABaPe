# Plan: WinRM certificate from the platform CA (B2) and Ansible host-key checks (B3)

> **Stage: Planning.** Batches B2 and B3 of [Planning_Next-Round-2026-11-08.md](Planning_Next-Round-2026-11-08.md). Both change how a deploy talks to a VM, and the Windows half cannot be proved without a live Windows VM, so this plan is written first and the choices are questions 222-226 in [Planning_Questions.md](Planning_Questions.md); every recommendation is the default if there is no answer.

## 0. Where it stands

| Thing | Today |
|---|---|
| A Windows VM's WinRM listener | HTTPS on 5986 with the self-signed certificate the template made. `labape_cert` (Windows) already requests a platform-CA certificate for the machine (certreq, machine store, FriendlyName "LABaPe host certificate") but the listener is not bound to it. |
| LABaPe's later connections to WinRM (commands, text sessions) | `vmexec` ignores validation and pins the SHA-256 of the certificate recorded after the deploy (`vmidentity`, question 198). |
| Ansible to Windows | `ansible_winrm_server_cert_validation: ignore` in the generated inventory. |
| Ansible to Linux | `host_key_checking = False` (`ansible.cfg`, `ANSIBLE_HOST_KEY_CHECKING=False` in the engine's runs), so any key is accepted on every run. |
| SSH from LABaPe's console and browser sessions | Checked against the recorded host keys (`vmidentity`), trust on first use. |

## 1. B2: bind the WinRM listener to the platform certificate (built 2026-11-11, steps 1, 3, 4; step 2 below is left to B3)

| Step | What |
|---|---|
| 1 | In `labape_cert/tasks/windows.yml`, after the certificate is accepted: find it by thumbprint and set the HTTPS listener to it in place (`winrm set winrm/config/Listener?Address=*+Transport=HTTPS @{CertificateThumbprint=...}`), not remove and create, so the running session survives. A renewal (the role already renews at 10 days) rebinds the same way. |
| 2 | **Not built (moves to B3):** the generated inventory sets `ansible_winrm_server_cert_validation: validate` and `ansible_winrm_ca_trust_path` (the platform root) for a Windows host once the certificate role has run in that deploy; the first contact, before the platform certificate exists, stays as it is. |
| 3 | `vmexec` validates against the platform root and the host's name or address (the certificate carries both) when the VM's record says `ca`; a record of kind pin (a VM without a platform certificate, or a CLI deploy) keeps the pin. `vmidentity` records which kind each VM is. |
| 4 | A VM whose listener certificate is the CA's but does not chain to the platform root is refused with the existing "possible impersonation" hint. |

Risk: rebinding the listener can drop the connection mid-play. Test on one lab Windows VM first (question 226), with a rollback: the role keeps the old thumbprint in `C:\ProgramData\LABaPe\tls\listener-previous.txt` and the next run restores it when the new certificate does not answer.

## 2. B3: Ansible's host-key checks

| Step | What |
|---|---|
| 1 | Deploy-time Ansible gets a per-job known_hosts file seeded from the recorded identities (`vmidentity` already builds one for the console). A VM with a record is checked strictly (`StrictHostKeyChecking=yes`); a VM without one is accepted on first contact and recorded at the end of the deploy, as today. |
| 2 | `host_key_checking = False` stays only in the CLI's `ansible.cfg` (a person at a terminal, no records), and the engine's runs set it from whether the VM has a record. |
| 3 | A mismatch fails the job for that VM with the same message as the console, naming `labape vm-identity forget` for a VM rebuilt outside LABaPe. A deploy of an environment forgets its VMs' old records first (already so), so a redeploy is not blocked. |
| 4 | Later Ansible runs against a deployed environment (add a host, package updates, rotation) check strictly; these are where a swapped VM matters. |

## 3. Questions (recommendations are the defaults)

| # | Question | Recommendation |
|---|---|---|
| 222 | Bind the WinRM listener to the platform certificate in place during the deploy (B2)? | Yes, in place, with the previous thumbprint kept for a rollback; renewals rebind the same way. |
| 223 | A VM with no platform certificate (CLI deploys, the CA down)? | Keep the self-signed listener and the pin, as now; the record says which. |
| 224 | Should Ansible check recorded SSH host keys strictly on every later run (B3)? | Yes for VMs that have a record; first contact still accepted and recorded. |
| 225 | What does a mismatch do? | Fails the job for that VM with the console's message and hint; nothing is changed on the VM. |
| 226 | May Claude rebind a listener on a lab Windows VM to test B2 (which VM)? | Yes, one throwaway VM, with the rollback in place; never a domain controller. |

## 4. B2 run live (2026-11-11)

A new throwaway environment `b2win` (one Windows 10 VM from `win10-packer-2026.10` on kvm1, DHCP) deployed with the change: the listener answered with the platform certificate, the VM's record was `ca`, `chains_to_platform_ca` was true, and a command through the console API validated against the platform root. `renew.ps1 -Force` issued a new certificate and moved the listener to it (matching thumbprints before and after); the command afterwards still worked, which a fingerprint pin would have refused. `tools/helpers/live/winrm_ca_check.py` repeats the check.
