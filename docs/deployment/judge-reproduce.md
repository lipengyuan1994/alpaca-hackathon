# Judge reproduction guide

This guide gives judges two deliberate paths through RegimeSwitch:

1. **Credential-free deterministic replay (recommended):** validates the
   resolver, defined-risk option planning, risk binding, immutable audit trail,
   and read-only replay behavior without any vendor account.
2. **Optional Gemini advisory startup:** lets a judge use their own Gemini API
   key to run the isolated advisory container. It never enables paper trading.

The first path is the submission's reproducible evaluation path. A provider
call cannot be reproduced byte-for-byte: model availability and generated prose
can vary. The implementation therefore freezes provider outputs as
`AgentThesisV1` artifacts for replay. The deterministic resolver accepts the
unchanged proposal or turns an advisory veto/failure into `NO_TRADE`; the model
cannot change strategy family, direction, DTE, strikes, ranking, size, price,
or risk limits.

## Safety boundary

- This repository is **paper-only**. There is no live-trading endpoint or
  credential path.
- Do not provide an Alpaca key to reproduce the default demo. Do not start the
  `execution` service for a judge demo.
- Never commit, paste into a terminal transcript, or place credentials in
  `.env`, source, tests, documentation, a Docker image, or the public UI.
- The public `api` is read-only. The Gemini key, if supplied, is mounted only
  into `agent`; the execution and public API roles never receive it.

## A. Credential-free deterministic replay

### Prerequisites

- Git.
- Python 3.12 and [`uv`](https://docs.astral.sh/uv/), or Docker Desktop for the
  optional read-only API container.
- On the maintainers' Apple Silicon path, use the native ARM64 `uv` commands in
  the repository README. The fixture tests themselves have no provider,
  broker, or database dependency.

Clone the repository, check out the exact tag or full SHA shown by the
submission, then confirm it locally. Do not assume that the default branch is
the submitted revision:

```zsh
git clone https://github.com/lipengyuan1994/alpaca-hackathon.git
cd alpaca-hackathon
git checkout <submitted-tag-or-full-sha>
git rev-parse HEAD
```

Install only the pinned project dependencies and run the complete test suite:

```zsh
uv sync --frozen
uv run pytest -q
```

Run the two deterministic decision fixtures:

```zsh
uv run paper-decision-worker
uv run paper-decision-worker --approved
uv run pytest -q tests/e2e/test_fixture_full_flow.py
```

Expected behavior:

| Command | Expected safe result | Network / credentials |
|---|---|---|
| `paper-decision-worker` | Visible deterministic `NO_TRADE` decision tape | None |
| `paper-decision-worker --approved` | Stops at `APPROVED_AND_ENQUEUED` in the transactional outbox | None; no Alpaca call |
| `test_fixture_full_flow.py` | Fake-broker lifecycle validates preflight and replay evidence | None |

For the read-only public API surface, run only the API container:

```zsh
docker compose -f infra/compose.yaml up --build api
```

No release-hash, Alpaca, database, or Gemini setting is required for this
command because it does not start the execution service. If execution were
started with blank release bindings, its own runtime gate exits before it can
construct a broker adapter.

In another terminal, verify the replay-only status endpoint:

```zsh
curl http://127.0.0.1:8000/v1/status
```

It reports `mode: REPLAY` and `authority: read-only`. Stop it with
`docker compose -f infra/compose.yaml stop api` when finished.

## B. Optional Gemini advisory container

This is optional. It is useful only when a judge wants to see the advisory
service start under an independently supplied Gemini credential. It is not
needed for the deterministic replay above.

Create an external secret directory. The example uses a personal config
directory, not the cloned repository:

```zsh
export REGIMESWITCH_SECRETS_DIR="$HOME/.config/regimeswitch-secrets"
install -d -m 700 "$REGIMESWITCH_SECRETS_DIR/llm"
```

With a local editor, create
`$REGIMESWITCH_SECRETS_DIR/llm/model_api_key.yaml` containing exactly this
schema, with the judge's own Gemini Developer API key substituted locally:

```yaml
gemini: "<judge-owned Gemini API key>"
```

Restrict the file after saving it:

```zsh
chmod 600 "$REGIMESWITCH_SECRETS_DIR/llm/model_api_key.yaml"
```

Start the pinned submission profile:

```zsh
export AGENT_MODEL_ID=gemini_3_6_flash
docker compose -f infra/compose.yaml up --build agent
```

The default profile and its protocol are pinned in
[`../../configs/advisory_models.yaml`](../../configs/advisory_models.yaml). The
agent starts only after reading the fixed YAML key `gemini` from the mounted
file. It has no published host port; its `/internal/v1/theses` route is
reachable only by the internal decision network. Its health endpoint can be
checked from inside the container without calling the provider:

```zsh
docker compose -f infra/compose.yaml exec agent python -c 'import json; from urllib.request import urlopen; assert json.loads(urlopen("http://127.0.0.1:8081/healthz").read()) == {"status": "ok"}'
```

The health check proves startup and secret-file loading; it does not make a
Gemini request. A real advisory request contains only sanitized market/strategy
input, requests structured JSON without tools, uses `store: false`, and is
fail-closed: unavailable credentials, timeout, provider error, or invalid
output becomes an advisory veto and deterministic `NO_TRADE`.

Stop the optional service after inspection:

```zsh
docker compose -f infra/compose.yaml stop agent
```

## C. Optional local audit database

The database is not required for fixture replay. To inspect the durable
PostgreSQL event/outbox schema with no Gemini or Alpaca credential, follow
[LOCAL_POSTGRES.md](LOCAL_POSTGRES.md). The setup generates separate local
database secrets outside the repository and starts only the private
`postgres` service.

## Troubleshooting and evidence

| Observation | Meaning / action |
|---|---|
| `NO_TRADE` | Valid, expected safe outcome; inspect the returned reason code and audit tape. |
| Missing Gemini file or invalid YAML key | Do not add an environment variable. Correct the external `model_api_key.yaml` file and restart only `agent`. |
| Gemini error, timeout, or invalid response | Expected fail-closed behavior: advisory veto then `NO_TRADE`; use deterministic fixtures for evaluation. |
| Need exact replay | Use the frozen thesis fixture; never issue a second provider call to recreate a past result. |
| Need a paper order | Out of scope for judge reproduction. It requires a separately controlled paper account, approved hashes, reconciliation, and private operator procedure. |

For secret-role details and the precise file mount boundary, see
[COMPOSE_SECRETS.md](COMPOSE_SECRETS.md). Do not include a judge's secret files
when packaging or uploading the repository.
