# Plan: overdue rotations, adding hosts, disk details and history, a Certificates page

> **Stage: Planning.** The requests of 2026-10-21 (after the browser run of v0.5.5), turned into batches with options and recommendations. Two are built (§0); the rest wait for the answers to questions 188-195 in [Planning_Questions.md](Planning_Questions.md). Each batch gets a section in [Planning_Web-Interface-Design.md](Planning_Web-Interface-Design.md) when it ships.

## 0. Built already (2026-10-21)

| Request | As built |
|---|---|
| The header bar stays at the top while scrolling | `App.tsx`: the top bar is `position="sticky"`. |
| Overdue secrets identified and highlighted | Secrets page (`pages/Secrets.tsx`): a rotation is **overdue** when its Next date passed more than an hour ago. The row is tinted, the Next cell shows the date in warning colour with an **overdue 2 days** chip (the tooltip says whether it waits for the maintenance window or needs a look at the worker), each section tab shows "(n overdue)", and a warning above the tabs lists every overdue secret. The three on OZ on 2026-10-21 (Platform CA key password, Master key, PostgreSQL password) are the "/ restart" ones that wait for the window. |

## 1. The batches

| Batch | What | Notes |
|---|---|---|
| **R2** Rotate overdue now | **Built 2026-10-21 (design §62.1).** A button on the overdue warning that rotates every overdue secret, one at a time, restarting services as needed after a confirmation. | Per-secret **Rotate now** exists already (`POST /api/admin/secrets/{id}/rotate`; restarting ones go through the update agent and turn maintenance mode on). R2 adds the bulk, ordered, confirmed version. Question 188. |
| **E2** Add a host to an existing environment | **Built 2026-10-21 (design §63).** An **Add host** action on a deployed environment: raise a group's count, or add a group that copies an existing one, then apply with a redeploy. | Question 187, answered on the recommended defaults. |
| **H3** Disk details, history and the link from an alert (**built 2026-10-21, design §64**) | In a KVM host's **Drives and volumes**, click a disk to open its page: identity, SMART attributes and a history chart. The `/dev/sdc` warning links to that page with a trend of pending and reallocated sectors. | Today SMART is read daily and only the newest reading is kept (`HostFacts.smart`), so there is no trend to draw: H3 starts a history table. Questions 189-191. |
| **C2** Certificates page for the platform CA (step-ca) | A **Certificates** page: see certificates, request one, renew or revoke, and see the CA itself. Two new roles; ordinary users see their own certificates. | The stack signs certificates today (`ca.sign`, `POST /api/engine/certificates`) but records none, and step-ca (open source) has no list to read. C2 starts a certificate register. Questions 192-195. |

Suggested order: **R2, E2, H3, C2** (small to large; H3's history table needs a few days of data before the chart is worth looking at, so start it early).

## 2. Design notes

**R2.** The overdue warning gets **Rotate overdue now**. Its confirmation lists the secrets, says which restart a service (and that maintenance mode turns on for them, pausing changes), and runs them one at a time: those that do not restart anything first, then the restarting ones in a fixed order with the **Master key** and the **CA key password** last. It stops at the first failure and says which secret failed and what is left. Each rotation is the existing job, so the audit log, backups and the "Last rotated" column stay as they are.

**E2.** The dialog offers *Add VMs to an existing group* (count up to the limit) or *Add a group* (the New environment group fields). It runs the same checks as creating an environment (names across the KVM host, capacity, quotas, addresses, disk floors, catalog choices by OS). It updates the stored spec and queues a normal redeploy; OpenTofu creates only the new VMs and Ansible's play for them (domain join, accounts, software) runs as for any host. Not offered on a destroyed environment.

**H3.** A table `disk_samples` (KVM host, device serial, time, the SMART numbers worth charting: reallocated, pending and uncorrectable sectors, temperature, power-on hours, health verdict) filled by the existing SMART collection (more often than daily; question 189) and by **Read now**. The disk page `/hosts/{id}/drives/{serial}` shows: model, serial, size, bus, the pool/volume/mount it belongs to; the SMART attribute table with each attribute's value, worst, threshold and raw; line charts of pending sectors, reallocated sectors and temperature over a range (7 days, 30 days, a year) with the warning thresholds marked; and the list of alerts raised for the disk. The `warnings()` entries for a disk carry the device and serial so the host page, the dashboard alert and the Monitoring list link to the disk page. The wording of the alert gains the change since the last reading ("pending sectors up 120 in 24 hours").

**C2.** A **Certificates** entry in the top bar for everyone (it shows only their own to ordinary users). Tabs: *My certificates*; *All certificates* (CA managers); *Request*; *Revoked*; *The CA* (CA managers: the root and intermediate certificates with fingerprint and expiry, download, the health check, the provisioners, issuance limits). A register table `certificates` (serial, subject, SANs, key type, issued, expires, status, requested by, environment and host if the engine asked, how it was requested) filled by every signing; the stack's own certificates (web, syslog, stores) are added by reading their files. Requesting: paste or upload a CSR, or let LABaPe make the key pair and offer the key and certificate as a one-time download (the key is not kept). Names are limited by a setting (allowed domains and addresses) and by the requester's role; lifetimes have a ceiling. Renewal makes a new certificate with the same names; revocation marks it in the register and runs `step ca revoke` (passive revocation).

**Roles.** `ca_admin` manages the CA (everything above, the settings, revoking anyone's certificate) and `ca_requester` may request certificates (and sees and renews their own). Both are added to `ROLES` with default Authentik groups `labape-ca-admins` and `labape-ca-requesters`, like the others. Without either role a user sees the *My certificates* list: the certificates they requested and the ones issued to hosts of environments they own. Admins hold both implicitly.

## 3. Open questions (188-195)

| Q | Question | Recommended |
|---|---|---|
| 188 | **Rotate overdue now (R2).** How should the bulk action behave? | The ordered, confirmed, stop-on-failure version above, restarting services only after the confirmation, with maintenance mode on for the restarting ones. No separate "restart all services" button. |
| 189 | **How often is SMART read, and how long is it kept (H3)?** | Every 6 hours (a `smartctl -A` is cheap), plus Read now; keep 365 days (a setting in Settings → History). Daily is too coarse for pending sectors, which can jump in a day. |
| 190 | **Which disks get a page (H3)?** | Physical drives listed on a KVM host or stack host (as in Drives and volumes now). VM disk images are not drives; they keep their rows under the VM. |
| 191 | **What does the alert link show (H3)?** | The disk's page, scrolled to the chart of the attribute that raised the alert (pending or reallocated sectors), over the last 30 days, with the change since the previous reading in the alert text. |
| 192 | **How are certificates requested (C2)?** | Both: a pasted or uploaded CSR, and a key pair made by LABaPe with a one-time download. ACME stays a direct step-ca matter, outside LABaPe. |
| 193 | **Who sees what (C2)?** | **Answered:** admins see all certificates; users see the certificates related to their environments; the auditor role sees all. `ca_admin` manages the CA (settings, revoking); `ca_requester` requests certificates. |
| 194 | **The register (C2).** step-ca has no list for LABaPe to read. | LABaPe records every certificate it signs from now on, and backfills the stack's own (web, syslog, stores) from their files. Certificates signed elsewhere with `step` are unknown to the register. |
| 195 | **Revocation and limits (C2).** | Revoke = mark in the register and `step ca revoke` (passive revocation: clients that never check revocation still accept it, and the page says so). Allowed names come from a setting (default: the stack's names and the lab domain); the longest lifetime is a setting (default 90 days). |
