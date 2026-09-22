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
service=t08
config=/app/configs/live/t08_tecl.yaml
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
old="$(ETF_LIVE_IMAGE="$image" ETF_LIVE_ENABLED=1 docker compose -f "$compose" ps -q "$service")"
old_image=""
if [ -n "$old" ]; then
  old_image="$(docker inspect --format '{{.Config.Image}}' "$old")"
  docker stop --time 30 "$old"
fi
wait_healthy() {
  candidate_image="$1"
  container="$(ETF_LIVE_IMAGE="$candidate_image" ETF_LIVE_ENABLED=1 docker compose -f "$compose" ps -q "$service")"
  if [ -z "$container" ]; then
    return 1
  fi
  attempt=0
  while [ "$attempt" -lt 12 ]; do
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$container")"
    if [ "$health" = healthy ]; then
      return 0
    fi
    if [ "$health" = unhealthy ]; then
      return 1
    fi
    attempt=$((attempt + 1))
    sleep 5
  done
  return 1
}
restore() {
  if [ -z "$old_image" ]; then
    return 0
  fi
  ETF_LIVE_IMAGE="$image" ETF_LIVE_ENABLED=0 docker compose -f "$compose" down --remove-orphans >/dev/null 2>&1 || true
  if ETF_LIVE_IMAGE="$old_image" ETF_LIVE_ENABLED=1 docker compose -f "$compose" up -d --no-build --remove-orphans "$service" && wait_healthy "$old_image"; then
    echo ETF_LIVE_PREVIOUS_IMAGE_RESTORED >&2
  else
    echo ETF_LIVE_PREVIOUS_IMAGE_RESTORE_FAILED >&2
  fi
}
# The legacy l11 preflight remains a compatibility path; the selected service is T08.
if ! ETF_LIVE_IMAGE="$image" ETF_LIVE_ENABLED=0 docker compose -f "$compose" run --rm --no-deps "$service" preflight --config "$config"; then restore; echo ETF_LIVE_PREFLIGHT_BLOCKED >&2; exit 2; fi
if ! ETF_LIVE_IMAGE="$image" ETF_LIVE_ENABLED=1 docker compose -f "$compose" up -d --no-build --remove-orphans "$service"; then
  restore
  echo ETF_LIVE_DEPLOY_START_FAILED >&2
  exit 2
fi
if ! wait_healthy "$image"; then
  restore
  echo ETF_LIVE_DEPLOY_HEALTHCHECK_FAILED >&2
  exit 2
fi
if ! ETF_LIVE_IMAGE="$image" ETF_LIVE_ENABLED=1 docker compose -f "$compose" exec -T "$service" preflight --config "$config"; then
  restore
  echo ETF_LIVE_POSTSTART_PREFLIGHT_BLOCKED >&2
  exit 2
fi
ETF_LIVE_IMAGE="$image" ETF_LIVE_ENABLED=1 docker compose -f "$compose" ps
tmp_current="${current}.tmp"
printf '%s\n' "$image" > "$tmp_current"
chmod 0600 "$tmp_current"
mv "$tmp_current" "$current"
echo ETF_LIVE_DEPLOYED
