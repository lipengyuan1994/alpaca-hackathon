# syntax=docker/dockerfile:1
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS etf-live

ARG TARGETARCH
ARG VCS_REF=unknown
RUN test "$TARGETARCH" = "amd64"
LABEL org.opencontainers.image.source="https://github.com/lipengyuan1994/alpaca-hackathon" \
      org.opencontainers.image.revision="$VCS_REF"
RUN test -f /usr/share/zoneinfo/America/New_York \
    && groupadd --gid 10002 etf-live \
    && useradd --uid 10002 --gid 10002 --no-create-home --home-dir /app etf-live
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=America/New_York \
    ETF_LIVE_ENABLED=0 \
    ETF_LIVE_CONFIG=/app/configs/live/l11_tqqq_soxl.yaml \
    ETF_LIVE_SECRETS_ROOT=/run/etf-live-secrets \
    ETF_LIVE_STATE_ROOT=/var/lib/alpaca-etf-live/l11_tqqq_soxl
COPY packages/contracts packages/contracts
COPY packages/runtime_secrets.py packages/runtime_secrets.py
COPY packages/etf_live packages/etf_live
COPY configs/live/l11_tqqq_soxl.yaml configs/live/l11_tqqq_soxl.yaml
COPY pyproject.toml pyproject.toml
COPY infra/etf-live/entrypoint.sh /usr/local/bin/etf-live-entrypoint
RUN pip install --no-cache-dir \
      "anyio==4.14.2" "annotated-types==0.8.0" \
      "httpx==0.28.1" "pydantic==2.13.5" "pydantic-core==2.46.5" \
      "PyYAML==6.0.3" "certifi==2026.7.22" "httpcore==1.0.9" \
      "h11==0.16.0" "idna==3.19" "sniffio==1.3.1" \
      "typing-extensions==4.16.0" "typing-inspection==0.4.4" \
    && chmod 0555 /usr/local/bin/etf-live-entrypoint \
    && mkdir -p /app/configs/live /app/artifacts /var/lib/alpaca-etf-live/l11_tqqq_soxl \
    && chown -R 10002:10002 /app
USER 10002:10002
ENTRYPOINT ["/usr/local/bin/etf-live-entrypoint"]
