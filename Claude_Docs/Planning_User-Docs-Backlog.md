# User-docs backlog

User-visible changes not yet covered in `User_Docs/`. One line each; remove a line once a user doc covers it.
- Web-Interface-Guide: **Delete** on a destroyed environment's page (what goes, what stays, the name is free again).
- Web-Interface-Guide: environment accounts' **Sudo, no password** column (Linux local accounts; one managed sudoers file).
- Web-Interface-Guide: the New environment form refuses a disk below the OS minimum (Windows 11: 64 GB).
- API tokens page: **Show revoked and expired** (hidden by default) and the retention in Settings → History; filter drop-downs apply when a choice is made; New environment checks fields as you type; the accounts dialog names the VMs beside each role.
- New environment: the Role box follows the OS; the accounts dialog clears passwordless sudo when the last Linux host is unticked.
- Secrets: overdue rotations are highlighted (tinted row, overdue chip, tab count and a warning); the top bar is sticky.
- Secrets: **Rotate overdue now** (what it does, the confirmation that services will stop, the order, stopping at a failure, the progress banner) (design §62.1).
- Environment page: **Add host** (more hosts in a group, or a new group like an existing one; the checks; that it starts a redeploy) (design §63).
- KVM hosts: the drive page (SMART history charts, when the numbers moved, attribute table), the links to it from Drives and volumes and from a drive's alert, SMART every 6 hours, and the Drive history retention in Settings → History (design §64).
- Certificates page (list and filters, details, request with or without a CSR, renew, revoke and what passive revocation means, the CA tab and its settings), the roles `ca_admin` and `ca_requester` and their Authentik groups, who sees which certificates (design §65).
- Certificates: the Issued column. Dashboard: the linked-stacks card of the stack you are on says **you are here**.
- New environment: the Domain choice (Member / Standalone) on a host group, when it applies (design §66).
- New environment: **Save as draft** (what a draft is, who sees it, that it takes no quota), **Edit draft** and **Deploy** on its page, and **Duplicate** (what is and isn't copied) (design §66).
- Environment page → Accounts: **Only these VMs** per local account (blank = every VM of the roles) (design §66).
- Certificates: the **Attributes** choices on Request, the Uses row in a certificate's details, and The CA tab's "applies extra attributes" switch with the one-time CA command (design §66).
