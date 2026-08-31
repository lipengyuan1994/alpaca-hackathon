# Local Docker PostgreSQL

The paper runtime needs a durable database for its event ledger, transactional
outbox/inbox, idempotency keys, risk reservations, execution leases,
reconciliation state, final-flatten state, one daily economic-context cache,
and the per-signal placement audit. This is therefore a runtime requirement
for a real paper run, but not for the offline fixture and replay paths.

For this Mac, PostgreSQL runs as the `postgres` Compose service rather than as
a host Homebrew service. The service uses a digest-pinned official PostgreSQL
18.3 image, defaults to native `linux/arm64`, publishes no host port, and lives
only on `database-internal`. The decision, economic-context, and execution
workers are the only application roles on that network. A judge on another
architecture can set
`REGIMESWITCH_DOCKER_PLATFORM` to its native Linux platform before building.

## One-time local setup

Docker Desktop must be running. Confirm the local Docker engine is native
before creating the database:

```zsh
docker version --format '{{.Server.Os}}/{{.Server.Arch}}'
```

Create the three database-specific secret files in the existing external secret
directory. This script uses secure random values, creates only new files, and
prints file names rather than secret values.

```zsh
UV_CACHE_DIR="$PWD/.uv-cache" UV_PYTHON_INSTALL_DIR="$PWD/.uv-python-arm64" /opt/homebrew/bin/uv run --frozen python scripts/provision_local_postgres_secrets.py --secrets-dir /Users/lipengyuan/.config/great_secrets
```

It creates the following `0600` files, with the `postgres` directory held at
`0700`:

| File | Consumer | Purpose |
|---|---|---|
| `postgres/bootstrap_password` | `postgres` only | Bootstrap administrator password; never mounted into an application container |
| `postgres/execution_password` | `postgres` only during initial bootstrap | Password for the non-superuser execution database role |
| `execution_database_url` | `decision`, `economic-context`, and `execution` | Internal Compose DSN for the private runtime database role |

Start and inspect only the database service. This command does not run the
execution worker and does not contact Alpaca or the advisory provider.

```zsh
docker compose -f infra/compose.yaml up --build --wait postgres
docker compose -f infra/compose.yaml ps postgres
docker compose -f infra/compose.yaml exec postgres psql --username regimeswitch_postgres_admin --dbname regimeswitch -c '\dt'
```

On its first start with an empty `postgres_data` volume, the image creates the
`regimeswitch_execution` role and runs `001_initial.sql` followed by
`002_runtime_safety.sql`, followed by `003_economic_context.sql`. The role is
a non-superuser with only `CONNECT` to the application database, schema
`USAGE`, and table `SELECT`/`INSERT`/`UPDATE`/`DELETE` for the runtime
ledger/context/audit. It cannot create roles, databases, schemas, tables, or
temporary tables, and it cannot connect to PostgreSQL's default databases.

The database DSN uses `sslmode=disable` only because its traffic never leaves
the internal Compose bridge and the service has no host port. A hosted
deployment must instead use a provider-issued TLS DSN, a managed backup policy,
and separately provisioned runtime and read-only identities.

## Reproducible judge setup

A fresh clone does not need Gemini or Alpaca credentials to reproduce the
database and schema. Choose an external, non-repository directory and point
Compose at it before running the same provision-and-start commands:

```zsh
export REGIMESWITCH_SECRETS_DIR="$HOME/.config/regimeswitch-secrets"
UV_CACHE_DIR="$PWD/.uv-cache" UV_PYTHON_INSTALL_DIR="$PWD/.uv-python-arm64" /opt/homebrew/bin/uv run --frozen python scripts/provision_local_postgres_secrets.py --secrets-dir "$REGIMESWITCH_SECRETS_DIR"
docker compose -f infra/compose.yaml up --build --wait postgres
```

Do not add vendor API secrets merely to demonstrate the database. The full
paper runtime remains fail-closed until its separate approved release hashes,
paper-account credentials, reconciliation, and operator controls are present.

The initialization scripts intentionally run only on an empty volume. Do not
replace the generated database secret files while the existing volume remains;
that would make the DSN and role password diverge. For a disposable local
database reset, explicitly stop the service and remove only its named volume
after preserving any audit evidence you need. Do not use this reset procedure
for a paper run or its retained audit trail.

## Existing-volume migration

`003_economic_context.sql` creates `daily_economic_context_v1` and
`signal_decision_audit_v1`. It runs automatically only for a newly initialized
volume. For an existing retained database, rebuild the PostgreSQL image and
apply that additive migration through the normal administrator migration
process before starting the new decision/economic roles. The local reference
command is:

```zsh
docker compose -f infra/compose.yaml exec postgres \
  psql --username regimeswitch_postgres_admin --dbname regimeswitch \
  --file /docker-entrypoint-initdb.d/003_economic_context.sql
```

Verify the two tables appear in `\dt` and retain the deployment's normal
backup/audit evidence before applying any schema change. The migration creates
tables and indexes; it does not reset the volume or delete retained evidence.
