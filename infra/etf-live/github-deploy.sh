#!/bin/bash
set -euo pipefail
umask 077

digest="${1:?full image digest required}"
: "${DEPLOY_HOST:?DEPLOY_HOST is required}"
: "${DEPLOY_USER:?DEPLOY_USER is required}"
: "${DEPLOY_KEY:?DEPLOY_KEY is required}"
: "${DEPLOY_KNOWN_HOSTS:?DEPLOY_KNOWN_HOSTS is required}"
: "${GHCR_TOKEN:?GHCR_TOKEN is required}"

case "$digest" in
  sha256:[0-9a-f][0-9a-f]*) ;;
  *) echo ETF_LIVE_IMAGE_DIGEST_INVALID >&2; exit 64 ;;
esac
if [ "${#digest}" -ne 71 ]; then
  echo ETF_LIVE_IMAGE_DIGEST_INVALID >&2
  exit 64
fi

install -m 700 -d "$HOME/.ssh"
key_file="$HOME/.ssh/etf-live-deploy-key"
known_hosts_file="$HOME/.ssh/etf-live-known-hosts"
printf '%s\n' "$DEPLOY_KEY" > "$key_file"
chmod 0600 "$key_file"
printf '%s\n' "$DEPLOY_KNOWN_HOSTS" > "$known_hosts_file"
chmod 0600 "$known_hosts_file"

ssh_args=(-i "$key_file" -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$known_hosts_file")
target="${DEPLOY_USER}@${DEPLOY_HOST}"
cleanup() {
  ssh "${ssh_args[@]}" "$target" ghcr-logout >/dev/null 2>&1 || true
}
trap cleanup EXIT

printf '%s' "$GHCR_TOKEN" | ssh "${ssh_args[@]}" "$target" ghcr-login
ssh "${ssh_args[@]}" "$target" deploy "ghcr.io/lipengyuan1994/alpaca-hackathon-etf-live@${digest}"
