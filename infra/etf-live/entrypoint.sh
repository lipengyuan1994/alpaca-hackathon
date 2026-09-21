#!/bin/sh
set -eu
config="${ETF_LIVE_CONFIG:-/app/configs/live/l11_tqqq_soxl.yaml}"
if [ "$#" -gt 0 ]; then
  exec python -m packages.etf_live.cli "$@"
fi
if [ "${ETF_LIVE_ENABLED:-0}" != "1" ]; then
  exec python -m packages.etf_live.cli status --config "$config"
fi
test -f /etc/etf-live/enabled || {
  echo ETF_LIVE_ENABLE_FILE_MISSING >&2
  exit 2
}
test "$(cat /etc/etf-live/enabled)" = 1 || {
  echo ETF_LIVE_ENABLE_FILE_NOT_ONE >&2
  exit 2
}
while :; do
  python -m packages.etf_live.cli run-once --config "$config" || true
  sleep "${ETF_LIVE_POLL_SECONDS:-15}"
done
