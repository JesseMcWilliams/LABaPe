# User-docs backlog

User-visible changes not yet covered in `User_Docs/`. One line each; remove a line once a user doc covers it.

- Browser SSH and RDP sessions: copy and paste text both ways (remote copy lands on your clipboard, click the page once if the browser holds it back; your clipboard goes to the remote side when you press Ctrl or use the new **Paste** button, then paste in the session (RDP: Ctrl+V; SSH: Ctrl+Shift+V or a middle click); the browser may ask to allow reading the clipboard). Files and images are not copied. (Web-Interface-Guide, remote sessions.)
- Remote sessions: a **recording player** link downloads one HTML file that plays a downloaded `.guac` recording offline (open the file, choose or drop the recording; nothing is uploaded). (Web-Interface-Admin, remote access.)

- Admin Dashboard: a **Fix** button on a failing Directory (group search) or Email (SMTP) check asks for the credentials it needs (an Authentik admin API token, or the mail server details), repairs the integration and forgets them. (Web-Interface-Admin, dashboard.)
- Environment templates: **Save as template** on an environment (name, description, share, which values can change), **Start from a template** on the New environment form (fills the form; warns about a template, network or catalog item the host lacks), the **Templates** page, and three starters (Domain with members, Three-tier Linux standalone, Kerberos lab). (Web-Interface-Guide, creating an environment.)

- Environment templates, second round: **New from parts** (a template made of other templates, each with a prefix), **Export** / **Export all** / **Import** as YAML to move templates between stacks. (Web-Interface-Guide, creating an environment.)
- Admin Dashboard: a **Fix** button on a failing Platform CA or secret store (OpenBao, Conjur) check: restart it (a store is unsealed again) or, for a store, give LABaPe a new login. Needs the update agent (reinstall it with `deploy/update-agent/install.sh` after updating). (Web-Interface-Admin, dashboard.)
