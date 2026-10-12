# OpenBao for tools/secrets-test: one-node integrated (Raft) storage on the
# openbao-data volume (OpenBao 2 has no "file" storage), TLS with the
# platform CA's certificate (tls/, written by deploy.sh).
storage "raft" {
  path    = "/openbao/file"
  node_id = "secrets-test"
}

listener "tcp" {
  address       = "0.0.0.0:8200"
  tls_cert_file = "/openbao/tls/server.crt"
  tls_key_file  = "/openbao/tls/server.key"
}

api_addr      = "https://127.0.0.1:8200"
cluster_addr  = "https://127.0.0.1:8201"
ui            = true
disable_mlock = true
