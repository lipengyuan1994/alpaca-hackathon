#!/bin/sh
set -eu
role_name="${REGIMESWITCH_READONLY_ROLE:?REGIMESWITCH_READONLY_ROLE_REQUIRED}"
password_file="${REGIMESWITCH_READONLY_PASSWORD_FILE:?REGIMESWITCH_READONLY_PASSWORD_FILE_REQUIRED}"
if [ ! -r "$password_file" ]; then
    echo "REGIMESWITCH_READONLY_PASSWORD_FILE_UNREADABLE" >&2
    exit 1
fi
if [ -z "$(tr -d '\r\n' < "$password_file")" ]; then
    echo "REGIMESWITCH_READONLY_PASSWORD_EMPTY" >&2
    exit 1
fi
psql --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --set=role_name="$role_name" <<'SQL'
\set role_password `tr -d '\r\n' < "$REGIMESWITCH_READONLY_PASSWORD_FILE"`
SELECT format(
    'CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 4 PASSWORD %L',
    :'role_name',
    :'role_password'
)
WHERE NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = :'role_name'
)
\gexec
SQL