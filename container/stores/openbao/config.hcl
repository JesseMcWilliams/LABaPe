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
}

api_addr      = "https://labape-openbao:8200"
cluster_addr  = "https://labape-openbao:8201"
ui            = false
disable_mlock = true
