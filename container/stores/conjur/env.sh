# Conjur takes its data key and database URL only from the environment:
# sourced by the container's entrypoint and by stores.sh's conjurctl calls.
CONJUR_DATA_KEY="$(cat /run/stores/conjur_data_key)"
DATABASE_URL="postgres://postgres:$(cat /run/stores/conjur_db_password)@labape-conjur-db/postgres"
export CONJUR_DATA_KEY DATABASE_URL
