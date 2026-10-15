# The app's policy (the API and worker): catalog secrets under the labape/ KV v2 mount.
path "labape/data/*"     { capabilities = ["create", "read", "update", "delete", "list"] }
path "labape/metadata/*" { capabilities = ["read", "list", "delete"] }
path "labape/metadata"   { capabilities = ["list"] }
path "auth/token/lookup-self" { capabilities = ["read"] }
# Rotating its own AppRole secret ID (design §25.3)
path "auth/approle/role/labape/secret-id"         { capabilities = ["update"] }
path "auth/approle/role/labape/secret-id/destroy" { capabilities = ["update"] }

# People's access to their environments' folders (design §49.2, V1b). LABaPe keeps the owners only:
# an identity entity per owner signing in with OIDC (labape-user-<username>, or the one OpenBao made
# at their first sign-in), external groups for owner groups (labape-group-<Authentik group>), and the
# members of each environment's group (labape-env-<name>, labape-admins). It can't write a policy or
# put one on a group (no "policies" parameter): stores.sh people-policies does that, with the root
# token, from a fixed template, when the update agent asks. So LABaPe can at most add itself to a
# group that reads an environment's folder, which it can read anyway.
path "sys/policies/acl" { capabilities = ["list"] }
path "sys/auth"         { capabilities = ["read"] }
path "identity/entity/name/labape-user-*" {
  capabilities       = ["create", "read", "update"]
  allowed_parameters = { "metadata" = [] }
}
path "identity/entity-alias" {
  capabilities       = ["create", "update"]
  allowed_parameters = { "name" = [], "canonical_id" = [], "mount_accessor" = [] }
}
path "identity/group/name/labape-*" {
  capabilities       = ["create", "read", "update"]
  allowed_parameters = { "type" = ["internal", "external"], "member_entity_ids" = [], "member_group_ids" = [], "metadata" = [] }
}
path "identity/group-alias" {
  capabilities       = ["create", "update"]
  allowed_parameters = { "name" = [], "canonical_id" = [], "mount_accessor" = [] }
}
path "identity/lookup/entity" { capabilities = ["update"] }
path "identity/lookup/group"  { capabilities = ["update"] }
