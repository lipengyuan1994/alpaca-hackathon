#!/bin/sh
# Create the only application database login. The administrator created by the
# official image remains deployment-only and is never mounted into a runtime
# application container.
set -eu

role_name="${REGIMESWITCH_EXECUTION_ROLE:?REGIMESWITCH_EXECUTION_ROLE_REQUIRED}"
password_file="${REGIMESWITCH_EXECUTION_PASSWORD_FILE:?REGIMESWITCH_EXECUTION_PASSWORD_FILE_REQUIRED}"

if [ ! -r "$password_file" ]; then
    echo "REGIMESWITCH_EXECUTION_PASSWORD_FILE_UNREADABLE" >&2
    exit 1
fi

# The provisioner writes a single hexadecimal line. Verify it without sending
# the value through a process argument; psql reads it into its private variable
# below, so it never appears in the image, a command line, or a log.
if [ -z "$(tr -d '\r\n' < "$password_file")" ]; then
    echo "REGIMESWITCH_EXECUTION_PASSWORD_EMPTY" >&2
    exit 1
fi

psql --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --set=role_name="$role_name" <<'SQL'
\set role_password `tr -d '\r\n' < "$REGIMESWITCH_EXECUTION_PASSWORD_FILE"`
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
