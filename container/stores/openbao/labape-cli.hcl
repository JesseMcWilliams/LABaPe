# The command line on the host (scripts/lib/store_get.py): read the engine
# secrets only, never write (design §26, question 102 c).
path "labape/data/platform/engine/vault_password"         { capabilities = ["read"] }
# The engine's current vault (encrypted with that password): `store_get.py vault-pass --rebuild` replaces a command-line
# vault that nothing can open with it. Conjur's policy already lets the command line read it.
path "labape/data/platform/engine/vault"                  { capabilities = ["read"] }
path "labape/data/platform/engine/vault_password_history" { capabilities = ["read"] }
path "labape/data/platform/engine/ssh_key"                { capabilities = ["read"] }
path "labape/data/platform/engine/ssh_key_pub"            { capabilities = ["read"] }
path "labape/data/platform/engine/ssh_history"            { capabilities = ["read"] }
