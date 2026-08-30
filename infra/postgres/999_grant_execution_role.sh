#!/bin/sh
# Grant only the DML and built-in locking access required by the runtime ledger.
# This runs after both schema migrations and revokes default PUBLIC access.
set -eu

role_name="${REGIMESWITCH_EXECUTION_ROLE:?REGIMESWITCH_EXECUTION_ROLE_REQUIRED}"

psql --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --set=role_name="$role_name" <<'SQL'
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', datname)
FROM pg_database
WHERE datallowconn
\gexec
REVOKE ALL ON SCHEMA public FROM PUBLIC;

SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'role_name')
\gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'role_name')
\gexec
SELECT format(
    'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I',
    :'role_name'
)
\gexec
SELECT format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
    current_user,
    :'role_name'
)
\gexec
SQL
