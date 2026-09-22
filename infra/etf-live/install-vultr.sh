#!/bin/sh
set -eu
umask 077

if [ "$(id -u)" -ne 0 ]; then
  echo RUN_AS_ROOT_REQUIRED >&2
  exit 77
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
install -d -m 0750 -o 10002 -g 10002 /etc/etf-live-secrets /etc/etf-live-config
install -d -m 0750 -o 10002 -g 10002 /var/lib/alpaca-etf-live/t08_tecl
install -d -m 0750 -o 10002 -g 10002 /var/lib/alpaca-etf-live/l11_tqqq_soxl
install -d -m 0755 /etc/etf-live /opt/alpaca-etf-live
install -m 0644 "$script_dir/compose.yaml" /opt/alpaca-etf-live/compose.yaml
install -m 0755 "$script_dir/deploy.sh" /usr/local/sbin/deploy-alpaca-etf-live
install -m 0755 "$script_dir/ssh-dispatch.sh" /usr/local/sbin/alpaca-etf-live-ssh-dispatch
if [ -f "$script_dir/../../configs/live/t08_tecl.yaml" ]; then
  install -m 0644 "$script_dir/../../configs/live/t08_tecl.yaml" /etc/etf-live-config/t08_tecl.yaml
fi
if [ -f "$script_dir/../../configs/live/l11_tqqq_soxl.yaml" ]; then
  install -m 0644 "$script_dir/../../configs/live/l11_tqqq_soxl.yaml" /etc/etf-live-config/l11_tqqq_soxl.yaml
fi
if [ ! -e /etc/etf-live/enabled ]; then
  install -o root -g root -m 0644 /dev/null /etc/etf-live/enabled
  printf '0\n' > /etc/etf-live/enabled
fi
echo ETF_LIVE_HOST_READY_DISABLED
