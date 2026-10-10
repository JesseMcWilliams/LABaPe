# Open questions

Decisions needed before the next pieces of work, numbered so they can be
answered by number. Background: Claude_Docs/Planning_Web-Interface-Options.md (web
interface), Claude_Docs/Reference_Backend-Parity.md (KVM vs Hyper-V),
Claude_Docs/Design_Base-Images.md (templates). Answered questions move into the
relevant design doc, not deleted from history.

## Web interface (M10)

1. Who will use it, and roughly how many people?
2. What authentication does it need: none (trusted network), one shared
   login, individual accounts, or Active Directory/LDAP sign-in?
3. Should some users only deploy from templates while others can edit
   them, or can everyone do everything?
4. Approach: is the recommended hybrid OK (Semaphore UI as the job runner
   now, a small custom app for template authoring later), or do you want
   one self-contained custom app?
5. Where should templates be stored: git-backed YAML in this repo
   (reviewable, consistent with everything else) or a database the web
   app owns?
6. Should the CLI (`scripts/deploy.sh` and friends) stay a first-class
   interface, with the web UI calling it rather than replacing it?
7. Where does the web interface run: on the libvirt host, or on its own
   VM?
8. How is it reached: LAN only, over a VPN, or from the internet? Is a
   self-signed HTTPS certificate acceptable, or is there an internal CA
   to use?
9. Any language or framework preference for the custom part (the
   research suggests Python/FastAPI to match the repo's helpers)?
10. Who may see an environment's credentials in the web UI: anyone who can
    see the environment, or only its owner/admins?

## KVM vs Hyper-V parity

11. Is Hyper-V still a target backend? If not, it could be marked
    "maintained as-is, RHEL family only" and the gaps left open.
12. If yes, where does closing the gaps rank against M7 (NAT mode),
    M8 (CI/lint), M9 (certificate authority) and M10 (web UI)?
13. If yes, is the suggested order right (interface parity and
    `deploy.sh` support first, then the boot-order fix, Windows, Debian
    family, templates, ownership guard), or is something more urgent?
14. Is there a physical Hyper-V host for this, or should development keep
    using the nested `hvhost1` VM on the libvirt host?

## Templates

15. How many dated versions of each template should be kept before old
    ones are deleted (§8 says "until nothing references them")?
16. Should template refreshes run on a schedule (e.g. monthly, or Patch
    Tuesday for Windows), or stay manual?
17. Windows updates during refresh: security, critical and rollups only
    (current default), or all available updates?
18. Which Phase A/B test templates should be deleted: `rocky9-base-2026.10`,
    `win2022-base-2026.10` (promoted), the `*-packer-2026.10` Core/early
    builds now superseded by `*-desktop-*` and `*-core-*`?
19. Windows Server edition: Standard (current) or Datacenter? The
    evaluation ISOs expire after 180 days: are licence keys or a KMS server
    available, or are short-lived evaluation VMs fine?

## Lab and operations

20. lab1 runs Server Core and was built from ISO. Keep it as it is, or
    rebuild it from Desktop Experience templates?
21. Should a block of addresses be reserved for test environments
    (offsets 100-199 hit a device on `.117` once), and recorded in
    environment.yml?
22. Is DHCP-mode addressing needed for anything yet (§17.4 defers it
    until there's a real need)?
23. Firefox can't install on Server 2019 Core: drop it from the example
    software manifest for `windows_server`, or leave it and accept the
    failure on 2019 Core hosts?
24. Should `Published_Docs/` end-user `.docx` guides start now (the
    user-docs backlog has accumulated about a dozen items)?
