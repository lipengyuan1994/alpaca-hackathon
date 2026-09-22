#!/bin/sh
set -eu
config="${ETF_LIVE_CONFIG:-/app/configs/live/t08_tecl.yaml}"
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
max_failures="${ETF_LIVE_MAX_CONSECUTIVE_FAILURES:-3}"
case "$max_failures" in
  ''|*[!0-9]*) echo ETF_LIVE_MAX_CONSECUTIVE_FAILURES_INVALID >&2; exit 2;;
esac
if [ "$max_failures" -lt 1 ]; then
  echo ETF_LIVE_MAX_CONSECUTIVE_FAILURES_INVALID >&2
  exit 2
fi
failures=0
while :; do
  if python -m packages.etf_live.cli run-once --config "$config"; then
    failures=0
  else
    failures=$((failures + 1))
    echo "ETF_LIVE_RUN_ONCE_FAILURE count=$failures max=$max_failures" >&2
    if [ "$failures" -ge "$max_failures" ]; then
      echo ETF_LIVE_CONSECUTIVE_FAILURE_LIMIT >&2
      exit 2
    fi
  fi
  sleep "${ETF_LIVE_POLL_SECONDS:-15}"
done
