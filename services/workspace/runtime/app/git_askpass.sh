#!/bin/sh
set -eu

case "${1:-}" in
  *Username*)
    printf '%s\n' 'x-access-token'
    ;;
  *)
    printf '%s\n' "${WORKSPACE_GIT_TOKEN:-}"
    ;;
esac
