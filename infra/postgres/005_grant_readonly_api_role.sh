#!/bin/sh
set -eu
role_name="${REGIMESWITCH_READONLY_ROLE:?REGIMESWITCH_READONLY_ROLE_REQUIRED}"
psql --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --set=role_name="$role_name" <<'SQL'
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'role_name')
\gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'role_name')
\gexec
SELECT format('GRANT SELECT ON ALL TABLES IN SCHEMA public TO %I', :'role_name')
\gexec
SELECT format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT SELECT ON TABLES TO %I',
    current_user,
    :'role_name'
)
\gexec
SQL