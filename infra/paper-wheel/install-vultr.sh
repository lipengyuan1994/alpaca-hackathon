#!/bin/sh
set -eu
umask 077

if [ "$(id -u)" -ne 0 ]; then
    echo "RUN_AS_ROOT_REQUIRED" >&2
    exit 77
fi

install -d -m 0755 /opt/alpaca-paper
install -d -m 0750 -o 10001 -g 10001 /var/lib/alpaca-paper/v13_5_qqq_market_hours_cash_secured
install -d -m 0750 -o 10001 -g 10001 /etc/alpaca-paper/secrets
install -m 0644 infra/paper-wheel/compose.yaml /opt/alpaca-paper/compose.yaml
install -m 0755 infra/paper-wheel/deploy.sh /usr/local/sbin/deploy-alpaca-paper

echo "PAPER_WHEEL_HOST_READY_DISABLED"

