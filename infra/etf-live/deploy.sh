#!/bin/sh
set -eu
umask 077
image="${1:-}"
case "$image" in ghcr.io/lipengyuan1994/alpaca-hackathon-etf-live@sha256:*) ;; *) echo ETF_LIVE_IMAGE_NOT_ALLOWED >&2; exit 64;; esac
digest="${image##*@sha256:}"
case "$digest" in *[!0-9a-f]*|'') echo ETF_LIVE_IMAGE_DIGEST_INVALID >&2; exit 64;; esac
if [ "${#digest}" -ne 64 ]; then
  echo ETF_LIVE_IMAGE_DIGEST_INVALID >&2
  exit 64
fi
compose=/opt/alpaca-etf-live/compose.yaml
current=/opt/alpaca-etf-live/current-image
test -f "$compose"
docker pull "$image"
if [ ! -f /etc/etf-live/enabled ] || [ "$(cat /etc/etf-live/enabled)" != 1 ]; then
  ETF_LIVE_IMAGE="$image" docker compose -f "$compose" down --remove-orphans >/dev/null 2>&1 || true
  tmp_current="${current}.tmp"
  printf '%s\n' "$image" > "$tmp_current"
  chmod 0600 "$tmp_current"
  mv "$tmp_current" "$current"
  echo ETF_LIVE_IMAGE_STAGED_DISABLED
  exit 0
fi
old="$(ETF_LIVE_IMAGE="$image" ETF_LIVE_ENABLED=1 docker compose -f "$compose" ps -q l11)"
[ -z "$old" ] || docker stop --time 30 "$old"
restore() { [ -z "$old" ] || docker start "$old" >/dev/null 2>&1 || true; }
if ! ETF_LIVE_IMAGE="$image" ETF_LIVE_ENABLED=0 docker compose -f "$compose" run --rm --no-deps l11 preflight --config /app/configs/live/l11_tqqq_soxl.yaml; then restore; echo ETF_LIVE_PREFLIGHT_BLOCKED >&2; exit 2; fi
ETF_LIVE_IMAGE="$image" ETF_LIVE_ENABLED=1 docker compose -f "$compose" up -d --no-build --remove-orphans
ETF_LIVE_IMAGE="$image" ETF_LIVE_ENABLED=1 docker compose -f "$compose" ps
tmp_current="${current}.tmp"
printf '%s\n' "$image" > "$tmp_current"
chmod 0600 "$tmp_current"
mv "$tmp_current" "$current"
echo ETF_LIVE_DEPLOYED
