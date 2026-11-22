# User-docs backlog

User-visible changes not yet covered in `User_Docs/`. One line each; remove a line once a user doc covers it.
- Hyper-V removal: the WSL install guides (`Install-OpenTofu-WSL.md`, `Install-Ansible-WSL.md`) are still a Hyper-V walkthrough under a banner saying so; retire or rewrite them for a libvirt control machine.
- Apply host setup (design §83): the SSH access section's button, the automatic run after an update, the Setting `hostsetup.auto`; add to Web-Interface-Admin.md next to the `Prereq.*` list.
- ACME clients (design §84): the CA tab's ACME default and longest lifetime (days, applied by the update agent), the list's **Refresh from the CA** button and the `acme`/`ca` sources; the CA's database is now in the stack's PostgreSQL (ACME clients register again once: delete Home Assistant's `/data/letsencrypt/accounts`); update the Certificates paragraphs of `Web-Interface-Admin.md` §21 and the Guide's §12.
