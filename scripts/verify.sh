#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-http://localhost:8080}"
echo "Checking ${BASE_URL}/health"
curl -fsS "${BASE_URL}/health"
echo
echo "Health check passed."
