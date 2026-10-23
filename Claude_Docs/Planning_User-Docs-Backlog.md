# User-docs backlog

User-visible changes not yet covered in `User_Docs/`. One line each; remove a line once a user doc covers it.

- Browser SSH and RDP sessions: copy and paste text both ways (remote copy lands on your clipboard, click the page once if the browser holds it back; your clipboard goes to the remote side when you press Ctrl or use the new **Paste** button, then paste in the session (RDP: Ctrl+V; SSH: Ctrl+Shift+V or a middle click); the browser may ask to allow reading the clipboard). Files and images are not copied. (Web-Interface-Guide, remote sessions.)
- Remote sessions: a **recording player** link downloads one HTML file that plays a downloaded `.guac` recording offline (open the file, choose or drop the recording; nothing is uploaded). (Web-Interface-Admin, remote access.)

- Admin Dashboard: a **Fix** button on a failing Directory (group search) or Email (SMTP) check asks for the credentials it needs (an Authentik admin API token, or the mail server details), repairs the integration and forgets them. (Web-Interface-Admin, dashboard.)
- Environment templates: **Save as template** on an environment (name, description, share, which values can change), **Start from a template** on the New environment form (fills the form; warns about a template, network or catalog item the host lacks), the **Templates** page, and three starters (Domain with members, Three-tier Linux standalone, Kerberos lab). (Web-Interface-Guide, creating an environment.)

- Environment templates, second round: **New from parts** (a template made of other templates, each with a prefix), **Export** / **Export all** / **Import** as YAML to move templates between stacks. (Web-Interface-Guide, creating an environment.)
- Admin Dashboard: a **Fix** button on a failing Platform CA or secret store (OpenBao, Conjur) check: restart it (a store is unsealed again) or, for a store, give LABaPe a new login. Needs the update agent (reinstall it with `deploy/update-agent/install.sh` after updating). (Web-Interface-Admin, dashboard.)

- Templates made of parts: **Edit** changes the parts (add, remove, prefix, label) and the values set for a part's groups (hosts, OS, network, CPUs, memory, disk). (Web-Interface-Guide, creating an environment.)

- Templates made of parts: each part can **follow the latest** version of its template or be **pinned** to one (the Version box in New from parts and Edit); a pinned part says when a newer version exists. Each change to a template's groups, parameters or parts is a new version. (Web-Interface-Guide, creating an environment.)
- Admin Dashboard: a **Fix** button on a failing Syslog check (server, port, protocol, facility; a test message is sent before anything is saved). (Web-Interface-Admin, dashboard.)
