#!/bin/sh
set -eu
set -f
set -- ${SSH_ORIGINAL_COMMAND:-}
case "${1:-}" in
  ghcr-login)
    test "$#" -eq 1
    exec sudo -n docker login ghcr.io --username lipengyuan1994 --password-stdin
    ;;
  deploy)
    test "$#" -eq 2
    case "$2" in
      ghcr.io/lipengyuan1994/alpaca-hackathon-etf-live@sha256:*) ;;
      *) echo ETF_LIVE_IMAGE_NOT_ALLOWED >&2; exit 64 ;;
    esac
    exec sudo -n /usr/local/sbin/deploy-alpaca-etf-live "$2"
    ;;
  ghcr-logout)
    test "$#" -eq 1
    exec sudo -n docker logout ghcr.io
    ;;
  *) echo ETF_LIVE_SSH_COMMAND_NOT_ALLOWED >&2; exit 77;;
esac
