# User-docs backlog

User-visible changes not yet covered in `User_Docs/`. One line each; remove a line once a user doc covers it.

- New environment: Time zone field (design §57); shown on the environment page; fixed at creation.
- Windows sign-on (Kerberos): what an admin sets up (DNS name, service account and keytab, Kerberos and LDAP sources in Authentik, role bindings for AD groups) and what a user's browser needs (design §58).
- Sign-in follows the name used: add a name to `LABAPE_ALT_NAMES`, then `web-tls.sh renew` re-signs the certificate and updates Authentik's redirect addresses.
- Secrets page: the Sign in as column (the user name to type for each account), and the .rdp file and credentials sheet naming the VM for a domain-joined machine's local Administrator.
- Environment page: **Preview redeploy** (which VMs a redeploy would reinstall, create or remove, and why; nothing is applied) and `deploy.sh --plan-only <file>` for the command line (design §56.1).
- Secrets → Health: the **by rotation** status (a secret that rotates daily, so its rotation is its check) next to **not in use**.
