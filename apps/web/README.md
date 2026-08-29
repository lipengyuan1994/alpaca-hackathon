# Public web role

The public dashboard consumes only `GET /v1/*` replay/read-model endpoints. It
does not contain broker credentials, control mutations, or operator controls.
The initial UI can be hosted as a static frontend; API schemas are exported from
the FastAPI application before a generated TypeScript client is added.
