# Compose file-secret deployment

Status: required for the paper-only runtime; no secret values belong in this repository.

`infra/compose.yaml` uses Docker Compose file secrets.  On this development
Mac, it defaults to `/Users/lipengyuan/.config/great_secrets`; set
`REGIMESWITCH_SECRETS_DIR` to override that location in another environment.
The directory is outside this checkout and must be created and populated by
the deployer, not by an application script or CI log.

The host directory must be mode `0700` and readable only by the deployer; each
file should be mode `0600`. Do not put it under the repository, use `.env` for
it, mount it into a build, or print its contents in a shell command.

| Host file | Fixed YAML key / value | Mounted into | Purpose |
|---|---|---|---|
| `llm/model_api_key.yaml` | `gemini` | `agent` only | API key for the selected Gemini advisory model |
| `alpaca/alpaca_api_key.yaml` | `paper_alpaca_api_key` | `execution` only | Alpaca paper API key |
| `alpaca/alpaca_api_key.yaml` | `paper_alpaca_api_secret` | `execution` only | Alpaca paper API secret |
| `alpaca/alpaca_api_key.yaml` | `paper_account_id` | `execution` only | Expected judged paper account identity |
| `alpaca/economic_data_api_key.yaml` | `economic_alpaca_api_key` | `economic-context` only | Dedicated Alpaca Market Data key for the once-daily context collector |
| `alpaca/economic_data_api_key.yaml` | `economic_alpaca_api_secret` | `economic-context` only | Matching dedicated Alpaca Market Data secret |
| `execution_database_url` | one raw DSN value | `decision`, `economic-context`, and `execution` | Private integrated runtime database connection |
| `postgres/bootstrap_password` | one raw password value | `postgres` only | Local Compose database bootstrap administrator password |
| `postgres/execution_password` | one raw password value | `postgres` only during first initialization | Password assigned to the least-privilege execution database role |

Inside a container, the files are mounted below `/run/secrets`. Application
code accepts only the corresponding `*_FILE` setting and rejects normal
secret-valued environment variables. The YAML selector paths are fixed in
source; no environment variable can choose another key, and the `Endpoint`
field in the Alpaca bundle is intentionally ignored. The only non-secret model
selection is `AGENT_MODEL_ID`, which must reference an enabled profile in
[`../../configs/advisory_models.yaml`](../../configs/advisory_models.yaml).
The `gemini` field in the model bundle must match that selected provider.
The release default is the pinned `gemini_3_6_flash` profile, which uses
Gemini's tool-free Interactions API with structured JSON output and
`store: false`.  The profile catalog binds both the model ID and wire protocol;
changing `AGENT_MODEL_ID` cannot select an arbitrary provider endpoint.

The `agent` service has no published port and receives no paper broker,
database, account, contract, sizing, price, or risk secret. It is reachable
only over the internal Compose network. The `economic-context` role receives a
separate data key, not the competition paper key, and contains only Alpaca
Market Data/news client code plus the database cache adapter. It has no
execution package, broker-egress network, or order-submission path. The
`execution` service does not receive an advisory-provider key. The public API
receives neither class of secret.

The current fixture/replay commands remain intentionally offline and use a
frozen thesis artifact.  They never invoke the `agent` service or a provider;
the internal `/internal/v1/theses` route is for the production decision-job
integration only.

For the local Docker database setup, generated database-only secret files, and
judge reproduction procedure, see [the local PostgreSQL runbook](LOCAL_POSTGRES.md).

An ordinary Docker bridge network is not an FQDN egress firewall. Before a
paper run, enforce an outbound policy outside Compose that allows the `agent`
role only to the selected provider HTTPS origin, the `economic-context` role
only to Alpaca's market-data/news origin plus the database endpoint, and the
`execution` role only to the paper Alpaca origin and required database
endpoint. Alpaca credentials are not assumed to be technically read-scoped, so
the separate collector key and external egress control are both required. A
provider error, timeout, schema error, unavailable secret, or unavailable daily
context must lead to a veto and then deterministic `NO_TRADE`.

Rotate one role's secret at a time, restart only that role, and retain the
model/profile identifier plus hash-bound thesis artifacts for audit.  Never
replay by making a new provider call.
