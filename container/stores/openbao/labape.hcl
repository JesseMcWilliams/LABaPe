# The app's policy (the API and worker): catalog secrets under the labape/ KV v2 mount.
path "labape/data/*"     { capabilities = ["create", "read", "update", "delete", "list"] }
path "labape/metadata/*" { capabilities = ["read", "list", "delete"] }
path "labape/metadata"   { capabilities = ["list"] }
path "auth/token/lookup-self" { capabilities = ["read"] }
# Rotating its own AppRole secret ID (design §25.3)
path "auth/approle/role/labape/secret-id"         { capabilities = ["update"] }
path "auth/approle/role/labape/secret-id/destroy" { capabilities = ["update"] }
