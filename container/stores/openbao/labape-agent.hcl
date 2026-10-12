# The host's update agent: the bootstrap secrets (master key, PostgreSQL and
# CA passwords) under their own KV v2 mount, which the app can't read (design §26).
path "labape-host/data/*"     { capabilities = ["create", "read", "update", "delete", "list"] }
path "labape-host/metadata/*" { capabilities = ["read", "list", "delete"] }
path "labape-host/metadata"   { capabilities = ["list"] }
path "auth/token/lookup-self" { capabilities = ["read"] }
