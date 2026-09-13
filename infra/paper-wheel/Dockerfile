# syntax=docker/dockerfile:1
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS paper-wheel

ARG TARGETARCH
ARG VCS_REF=unknown
RUN test "$TARGETARCH" = "amd64"

LABEL org.opencontainers.image.source="https://github.com/lipengyuan1994/alpaca-hackathon" \
      org.opencontainers.image.revision="$VCS_REF"

RUN test -f /usr/share/zoneinfo/America/New_York \
    && groupadd --gid 10001 paper-wheel \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /app paper-wheel

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PAPER_API_BASE_URL=https://paper-api.alpaca.markets \
    REGIMESWITCH_SECRETS_DIR=/run/paper-secrets

COPY packages/contracts packages/contracts
COPY packages/paper_wheel packages/paper_wheel
COPY packages/runtime_secrets.py packages/runtime_secrets.py
COPY apps/common apps/common
COPY configs/paper/v13_5_qqq.yaml configs/paper/v13_5_qqq.yaml
COPY pyproject.toml pyproject.toml
COPY infra/paper-wheel/entrypoint.sh /usr/local/bin/paper-wheel-entrypoint

RUN pip install --no-cache-dir \
        "alpaca-py==0.44.0" \
        "annotated-types==0.8.0" \
        "certifi==2026.7.22" \
        "charset-normalizer==3.5.1" \
        "idna==3.19" \
        "msgpack==1.2.2" \
        "numpy==2.5.2" \
        "pandas==3.0.5" \
        "pydantic==2.13.5" \
        "pydantic-core==2.46.5" \
        "python-dateutil==2.9.0.post0" \
        "pytz==2026.3.post1" \
        "PyYAML==6.0.3" \
        "requests==2.34.2" \
        "six==1.17.0" \
        "sseclient-py==1.9.0" \
        "typing-extensions==4.16.0" \
        "typing-inspection==0.4.4" \
        "urllib3==2.7.0" \
        "websockets==17.1" \
    && chmod 0555 /usr/local/bin/paper-wheel-entrypoint \
    && mkdir -p /app/artifacts/paper_wheel/v13_5_qqq_market_hours_cash_secured \
    && chown -R 10001:10001 /app/artifacts

USER 10001:10001
ENTRYPOINT ["/usr/local/bin/paper-wheel-entrypoint"]
