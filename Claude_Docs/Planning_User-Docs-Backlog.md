# User-docs backlog

User-visible changes not yet covered in `User_Docs/`. One line each; remove a line once a user doc covers it.
- KVM host settings: saving a changed VM, template or files folder checks it on the host and fixes its permissions (previewed first); **Check folders** and **Fix folders** buttons; AppArmor is reported, never changed (design §50.3a).
- Host → Drives and volumes: a drive with a SMART warning (reallocated or pending sectors, wear, failed health) is flagged there too: an alert on top, a coloured row with a FAILING or warning chip, and a "check it" chip beside a passed health.
