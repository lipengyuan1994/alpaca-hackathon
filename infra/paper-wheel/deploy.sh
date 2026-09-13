#!/bin/sh
set -eu
umask 077

compose_file="/opt/alpaca-paper/compose.yaml"
enabled_file="/etc/alpaca-paper/enabled"
image_file="/opt/alpaca-paper/current-image"
image="${1:-}"

case "$image" in
    ghcr.io/lipengyuan1994/alpaca-hackathon-paper-wheel@sha256:*) ;;
    *) echo "PAPER_WHEEL_IMAGE_NOT_ALLOWED" >&2; exit 64 ;;
esac

digest="${image##*@sha256:}"
case "$digest" in
    *[!0-9a-f]*|'') echo "PAPER_WHEEL_IMAGE_DIGEST_INVALID" >&2; exit 64 ;;
esac
if [ "${#digest}" -ne 64 ]; then
    echo "PAPER_WHEEL_IMAGE_DIGEST_INVALID" >&2
    exit 64
fi

test -f "$compose_file"
docker pull "$image"

if [ ! -f "$enabled_file" ] || [ "$(cat "$enabled_file")" != "1" ]; then
    temporary_image_file="${image_file}.tmp"
    printf '%s\n' "$image" > "$temporary_image_file"
    chmod 0600 "$temporary_image_file"
    mv "$temporary_image_file" "$image_file"
    echo "PAPER_WHEEL_IMAGE_STAGED_DISABLED image=$image"
    exit 0
fi

existing_container="$(PAPER_WHEEL_IMAGE="$image" PAPER_WHEEL_ENABLED=1 \
    docker compose --file "$compose_file" ps --quiet paper-wheel)"
if [ -n "$existing_container" ]; then
    docker stop --time 30 "$existing_container"
fi

restore_existing() {
    if [ -n "$existing_container" ]; then
        docker start "$existing_container" >/dev/null 2>&1 || true
    fi
}

if ! PAPER_WHEEL_IMAGE="$image" PAPER_WHEEL_ENABLED=0 \
    docker compose --file "$compose_file" run --rm --no-deps paper-wheel preflight; then
    restore_existing
    echo "PAPER_WHEEL_DEPLOY_PREFLIGHT_BLOCKED" >&2
    exit 2
fi
if ! PAPER_WHEEL_IMAGE="$image" PAPER_WHEEL_ENABLED=0 \
    docker compose --file "$compose_file" run --rm --no-deps paper-wheel verify-arm; then
    restore_existing
    echo "PAPER_WHEEL_DEPLOY_ARM_BLOCKED" >&2
    exit 2
fi

PAPER_WHEEL_IMAGE="$image" PAPER_WHEEL_ENABLED=1 \
    docker compose --file "$compose_file" up --detach --no-build --remove-orphans
PAPER_WHEEL_IMAGE="$image" PAPER_WHEEL_ENABLED=1 \
    docker compose --file "$compose_file" ps
PAPER_WHEEL_IMAGE="$image" PAPER_WHEEL_ENABLED=1 \
    docker compose --file "$compose_file" exec --no-TTY paper-wheel \
    python -m packages.paper_wheel.cli status \
    --config /app/configs/paper/v13_5_qqq.yaml

temporary_image_file="${image_file}.tmp"
printf '%s\n' "$image" > "$temporary_image_file"
chmod 0600 "$temporary_image_file"
mv "$temporary_image_file" "$image_file"
