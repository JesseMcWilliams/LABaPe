# User-docs backlog

User-visible changes not yet covered in `User_Docs/`. One line each; remove a line once a user doc covers it.
- Hyper-V removal: the WSL install guides (`Install-OpenTofu-WSL.md`, `Install-Ansible-WSL.md`) are still a Hyper-V walkthrough under a banner saying so; retire or rewrite them for a libvirt control machine.
- Apply host setup (design §83): the SSH access section's button, the automatic run after an update, the Setting `hostsetup.auto`; add to Web-Interface-Admin.md next to the `Prereq.*` list.
- Self-tests (plan: Planning_SMART-Self-Test-Schedule.md, design §76): the Admin → Self-tests page (stack default, per-host schedule, drives left out, warn switch, what the host really has, next run), the same panel on a host's Drives section, and the new wording of the "never tested" warning (info note while the first run is ahead); add a Self-tests section to Web-Interface-Admin.md (the Help link already points at it) and update the `Prereq.SmartTests` and **Self-tests** paragraphs.
