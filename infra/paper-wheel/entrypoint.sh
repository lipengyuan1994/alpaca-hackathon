#!/bin/sh
set -eu
umask 077

config_path="/app/configs/paper/v13_5_qqq.yaml"

if [ "$#" -gt 0 ]; then
    exec python -m packages.paper_wheel.cli "$@" --config "$config_path"
fi

if [ "${PAPER_WHEEL_ENABLED:-0}" != "1" ]; then
    echo "PAPER_WHEEL_CONTAINER_DISABLED" >&2
    exit 78
fi

while true; do
    local_day="$(TZ=America/New_York date +%u)"
    local_hhmm="$(TZ=America/New_York date +%H%M)"
    if [ "$local_day" -le 5 ] && [ "$local_hhmm" -ge 0830 ] && [ "$local_hhmm" -le 1630 ]; then
        python -m packages.paper_wheel.cli run-once --config "$config_path" || true
    fi
    sleep 60
done

