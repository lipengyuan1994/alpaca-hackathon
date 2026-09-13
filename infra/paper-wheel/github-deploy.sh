#!/bin/bash
set -euo pipefail
umask 077

image_digest="${1:-}"
: "${DEPLOY_HOST:?DEPLOY_HOST is required}"
: "${DEPLOY_USER:?DEPLOY_USER is required}"
: "${DEPLOY_KEY:?DEPLOY_KEY is required}"
: "${DEPLOY_KNOWN_HOSTS:?DEPLOY_KNOWN_HOSTS is required}"
: "${GHCR_TOKEN:?GHCR_TOKEN is required}"

case "$image_digest" in
    sha256:[0-9a-f][0-9a-f]*) ;;
    *) echo "IMAGE_DIGEST_INVALID" >&2; exit 64 ;;
esac
if [ "${#image_digest}" -ne 71 ]; then
    echo "IMAGE_DIGEST_INVALID" >&2
    exit 64
fi

install -m 700 -d "$HOME/.ssh"
printf '%s\n' "$DEPLOY_KEY" > "$HOME/.ssh/deploy_key"
chmod 600 "$HOME/.ssh/deploy_key"
printf '%s\n' "$DEPLOY_KNOWN_HOSTS" > "$HOME/.ssh/known_hosts"

ssh_target="${DEPLOY_USER}@${DEPLOY_HOST}"
ssh_args=(-i "$HOME/.ssh/deploy_key" -o BatchMode=yes -o StrictHostKeyChecking=yes)

cleanup() {
    ssh "${ssh_args[@]}" "$ssh_target" ghcr-logout >/dev/null 2>&1 || true
}
trap cleanup EXIT

printf '%s' "$GHCR_TOKEN" | ssh "${ssh_args[@]}" "$ssh_target" ghcr-login
ssh "${ssh_args[@]}" "$ssh_target" \
    "deploy ghcr.io/lipengyuan1994/alpaca-hackathon-paper-wheel@${image_digest}"

