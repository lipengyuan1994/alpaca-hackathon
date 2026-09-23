#!/bin/sh
set -eu
set -f

original_command="${SSH_ORIGINAL_COMMAND:-}"
set -- $original_command

case "${1:-}" in
    deploy)
        test "$#" -eq 2
        exec sudo -n /usr/local/sbin/alpaca-paper-deploy-session "$2"
        ;;
    *)
        echo "PAPER_WHEEL_SSH_COMMAND_NOT_ALLOWED" >&2
        exit 77
        ;;
esac
