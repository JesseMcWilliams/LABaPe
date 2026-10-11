# Certificate Authorities

Extends Claude_Docs/Design_System-Overview.md §19 — a `certificate_authority` role (future work, not
yet implemented) issuing trusted certs to hosts in the environment,
mirroring how `domain_controller` (§8) is "just a role" rather than a
fixed host type.

## 1. Two implementations, chosen by platform

| | Windows CA | Linux CA |
|---|---|---|
| Software | AD CS (Active Directory Certificate Services), Enterprise CA | [step-ca](https://smallstep.com/docs/step-ca/) (Smallstep) |
| Setup | `Install-WindowsFeature ADCS-Cert-Authority` + `Install-AdcsCertificationAuthority` | Single binary/service, config-file driven |
| Client enrollment | Autoenrollment via Group Policy, once domain-joined | ACME, or `step ca certificate` via the `step` CLI |
| Requires | A domain to enroll into (Enterprise CA, not Standalone) — so realistically pairs with an existing `domain_controller` | Nothing beyond the host itself |

A host carries the role as `roles: ["certificate_authority"]` (or
combined with others, §3) with a platform implied by its `os` the same
way every other role resolves — a `windows_server_2022` host with this
role gets the AD CS play, a Linux host gets the step-ca play.

## 2. Trust relationship between the two

This is the one real design decision here, resolved as: **if a Windows
CA is present in the environment, the Linux CA is subordinate to it. If
not, the Linux CA is its own root.**

- **Windows CA present**: step-ca is initialized in *intermediate* mode
  rather than generating its own self-signed root. It generates its own
  intermediate keypair and a CSR, which gets submitted to the AD CS
  Enterprise CA (`certreq`, or AD CS's web enrollment) and signed. The
  returned intermediate certificate becomes step-ca's `intermediate_ca`
  in its config — step-ca now issues leaf certs under a chain that
  terminates at the Windows root. One cross-trusted PKI: a cert issued
  by either CA validates against the same root, so a Linux service's
  cert is trusted by a Windows client (and vice versa) without any
  extra trust-store work beyond distributing the one Windows root.
- **No Windows CA**: step-ca generates and uses its own self-signed
  root, exactly as it would standalone. Only Linux hosts (and anything
  explicitly given step-ca's root) trust certs it issues.

**Ordering constraint** (same shape as §8's domain-controller-before-join
rule): when both roles are present, the Windows CA must be provisioned
and its Enterprise CA operational *before* the Linux CA's intermediate
CSR is submitted — a new dependency edge in the pipeline (Claude_Docs/Design_System-Overview.md §4),
conceptually "certificate_authority (Windows) → certificate_authority
(Linux)" the same way "domain_controller → everything else" already
works.

## 3. Role combination and cardinality

- `certificate_authority` combines freely with other roles, same
  flexible-role model as §9 — e.g. `[domain_controller,
  certificate_authority]` (CA co-located with the DC, the common small-lab
  pattern) or `[certificate_authority]` alone (dedicated CA host, closer
  to real-world PKI hygiene).
- **At most one Windows CA and one Linux CA per environment** — same
  singleton assumption §8 already makes for `domain_controller`. Multi-CA
  scenarios per platform aren't a goal here.

## 4. Open questions (not yet resolved)

- **Client trust distribution**: Windows domain members get the AD CS
  root automatically via Group Policy (autoenrollment already handles
  this). Linux hosts joining via `realmd` don't get anything automatic —
  the CA role's Ansible role will need to explicitly place the root CA
  (Windows root if subordinate, step-ca's own root otherwise) into each
  Linux host's trust store (`update-ca-trust`/`update-ca-certificates`
  depending on distro family). Not yet designed which stage of the
  pipeline (§4) this belongs to.
- **step-ca ACME for Windows clients**: step-ca can also serve ACME
  directly, which Windows clients could theoretically use instead of
  autoenrollment. Not needed for the subordinate case (AD CS
  autoenrollment already covers Windows domain members) — worth
  revisiting only if a scenario needs non-domain-joined Windows hosts to
  get certs too.
- **Renewal automation**: whether/how issued certs get renewed
  automatically (step-ca's own renewal tooling vs. AD CS autoenrollment's
  built-in renewal) isn't designed yet — likely "each platform's native
  mechanism," but not confirmed.
- **Where CA config lives**: whether the CA role needs its own
  per-environment manifest (parallel to `software-manifest.yml`/
  `directory-manifest.yml`) for things like "which hosts/services need a
  cert issued and for what SAN" — or whether that's driven by each
  service's own role instead (e.g. a web-server role requests its own
  cert as part of its own Ansible tasks). Leaning toward the latter
  (keeps the CA role itself simple — just "stand up a CA" — and pushes
  "get me a cert" to whatever role actually needs one), but not decided.

## 5. Platform CA for the LABaPe deployment (decided 2026-10-11: option A)

The request: a step-ca for the LABaPe deployment itself. Every host
gets a certificate from it, unless the host's environment includes a
CA. In that case the environment's CA gets a certificate from the
platform CA, and the environment's hosts use their own CA.

### 5.1 Options

| | A: platform root, environment CAs subordinate (recommended) | B: platform CA for infrastructure only | C: no platform CA |
|---|---|---|---|
| Web UI and KVM hosts | Certificates from the platform CA (ACME for Caddy) | Same as A | Caddy's internal CA or a supplied certificate, as today |
| VMs in an environment without a CA | A leaf certificate from the platform CA at deploy, the platform root in the trust store, and automatic renewal | Platform root trusted only; no certificate | Nothing |
| Environment with a CA (M9) | Its CA is an **intermediate under the platform root**: step-ca in intermediate mode, or AD CS as an Enterprise Subordinate CA whose CSR the runner has signed. Its hosts get certificates from it. | Its own independent root, as in §2 | As in §2 |
| Cross-environment trust | Everything chains to one root, so one trust anchor covers every environment and the web UI | One root per environment | One root per environment |
| Cost | Most work: a step-ca service, the token flow, renewal on Windows and Linux, signing subordinate CAs | Medium | None |

### 5.2 How option A would work

- **Where it runs:** a `step-ca` container in the web stack. The root
  key is created at install and stored encrypted in the secrets store.
  Online issuing is done by an intermediate. For a `kvm`-only install
  (§22.4 of the design doc), it runs on the KVM host instead.
- **Provisioners:**
  - ACME for Caddy (the web UI);
  - a JWK provisioner the runner uses to mint one-time tokens, one per VM per deploy, passed to Ansible;
  - a CA-signing template, used only by the runner, for environment CAs (`CA:true`, `pathlen:0`, name constraints set to the environment's domain).
- **Linux VMs:** the `step` CLI requests the certificate with the token,
  installs the root (`update-ca-trust` / `update-ca-certificates`), and
  a systemd timer runs `step ca renew`.
- **Windows VMs:** Ansible imports the root into `LocalMachine\Root` and
  installs the leaf certificate. Renewal is a scheduled task (the `step`
  CLI for Windows), or AD CS autoenrollment once the environment has
  its own CA.
- **Environment CAs:**
  - step-ca's role runs in intermediate mode, signed by the platform CA.
  - AD CS is installed with `-CAType EnterpriseSubordinateCa`. Its CSR is signed through the runner and installed with `certutil -installcert`, and the platform root is published to AD (`certutil -dspublish`), so Group Policy distributes it.
  - If an environment has both AD CS and step-ca, both become **siblings** directly under the platform root. This replaces §2's Linux-under-Windows chain, because a shared root already gives cross-trust.
- **Lifetimes (proposed):**
  - root: 10 years;
  - platform intermediate: 5 years;
  - environment intermediates: 1 year (an environment is short-lived);
  - leaf certificates: 30 days with automatic renewal.
- **Revocation:** none beyond short lifetimes (step-ca's passive
  revocation). Destroying an environment discards its CA key.

### 5.3 Status (2026-10-11)

The platform part is built (design §22.5): the step-ca service, signing through the web interface, VM certificates with renewal, root trust on every VM, and the web interface's certificate over ACME. Environment CAs as intermediates wait for the `certificate_authority` role (M9). The intermediate lasts 10 years (`step ca init`'s default), not the proposed 5.

### 5.4 What it changes in this proposal

- §2's trust rule becomes "every environment CA is subordinate to the
  platform root".
- §4's "client trust distribution" question is answered: the platform
  root goes into every VM's trust store at deploy.
- §4's "where CA config lives" question stays as leaned: roles request
  their own certificates.
