# Backlog

> **Stage: Planning.** Work that's agreed but deferred, so it isn't lost. Each line says where the
> detail is. Move a line into a plan (and out of here) when the work is picked up. User-visible
> changes waiting for user docs have their own list: [Planning_User-Docs-Backlog.md](Planning_User-Docs-Backlog.md).

## Deferred by the user (2026-10-12)

- **Hyper-V parity** (questions 11-14): Hyper-V stays a target backend. Closing the gaps is
  deferred and not ranked yet. The suggested order, from Reference_Backend-Parity.md: interface
  parity and `deploy.sh` support, the boot-order fix, Windows, the Debian family, templates, the
  ownership guard. Decide physical host vs the nested `hvhost1` VM when it starts.
- **Published `.docx` guides** (question 24): later. The Markdown guides in `User_Docs/` are the
  source.
- **Release v0.3.0**: later, when the user says so. The release key is in place
  (`deploy/allowed_signers`, 2026-10-12): tags are signed with it.
- **User-docs backlog**: later (its own list, above).
- **More accounts per environment** (question 113): add local and domain accounts to an
  environment beyond the built-in admin ones (created at deploy, listed on the credentials
  sheet, rotatable).
- Audit syslog sender: keep one TLS connection open to the collector instead of one per event (the collector logs "unexpected eof" for each; Testing_Troubleshooting-Log.md, Y2a item 3).
