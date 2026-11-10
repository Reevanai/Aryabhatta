#!/bin/sh
# Creates the login roles and databases on first start. Passwords come from the environment.
set -eu
psql -v ON_ERROR_STOP=1 --username postgres <<SQL
CREATE ROLE sentinel_owner LOGIN PASSWORD '${SENTINEL_DB_OWNER_PASSWORD}';
CREATE ROLE sentinel_app LOGIN PASSWORD '${SENTINEL_DB_APP_PASSWORD}';
CREATE ROLE sentinel_vault LOGIN PASSWORD '${SENTINEL_DB_VAULT_PASSWORD}';
CREATE ROLE sentinel_relay LOGIN PASSWORD '${SENTINEL_DB_RELAY_PASSWORD}';
CREATE ROLE keycloak LOGIN PASSWORD '${KEYCLOAK_DB_PASSWORD}';
CREATE DATABASE sentinel OWNER sentinel_owner;
CREATE DATABASE keycloak OWNER keycloak;
REVOKE ALL ON DATABASE sentinel FROM PUBLIC;
GRANT CONNECT ON DATABASE sentinel TO sentinel_app, sentinel_vault, sentinel_relay;
SQL
