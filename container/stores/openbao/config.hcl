# OpenBao in the LABaPe stack (Planning_Web-Interface-Design.md §26): one-node
# integrated (Raft) storage on the labape-openbao volume, TLS with a
# certificate from the platform CA (stores/tls/, written by stores.sh).
storage "raft" {
  path    = "/openbao/file"
  node_id = "labape"
}

listener "tcp" {
  address       = "0.0.0.0:8200"
  tls_cert_file = "/openbao/tls/server.crt"
  tls_key_file  = "/openbao/tls/server.key"
  # Don't ask clients for a certificate: browsers then offer the user's (a smart card on Windows).
  tls_disable_client_certs = true
}

# VMs sign in with their platform certificate (V2, design §49.3): this listener asks for it.
listener "tcp" {
  address       = "0.0.0.0:8210"
  tls_cert_file = "/openbao/tls/server.crt"
  tls_key_file  = "/openbao/tls/server.key"
}

api_addr      = "https://labape-openbao:8200"
cluster_addr  = "https://labape-openbao:8201"
# The web UI, for people signing in with OIDC (design §49.2, V1b; stores.sh sets the sign-in up).
ui            = true
disable_mlock = true
