#!/bin/sh
set -eu
set -f

original_command="${SSH_ORIGINAL_COMMAND:-}"
set -- $original_command

case "${1:-}" in
    ghcr-login)
        test "$#" -eq 1
        exec sudo docker login ghcr.io --username lipengyuan1994 --password-stdin
        ;;
    deploy)
        test "$#" -eq 2
        exec sudo /usr/local/sbin/deploy-alpaca-paper "$2"
        ;;
    ghcr-logout)
        test "$#" -eq 1
        exec sudo docker logout ghcr.io
        ;;
    *)
        echo "PAPER_WHEEL_SSH_COMMAND_NOT_ALLOWED" >&2
        exit 77
        ;;
esac

