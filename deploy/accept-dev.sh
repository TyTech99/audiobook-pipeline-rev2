#!/bin/sh
# Manual accept for a coordinator already listening on 127.0.0.1:8000.
# Does not install packages. Set API_KEY_OPERATOR before running.
set -eu
base="${COORDINATOR_URL:-http://127.0.0.1:8000}"
curl -fsS "$base/health"
echo
code=$(curl -s -o /dev/null -w "%{http_code}" "$base/api/v1/nodes")
if [ "$code" != "401" ]; then
  echo "expected 401 without a key, got $code" >&2
  exit 1
fi
curl -fsS -H "Authorization: Bearer ${API_KEY_OPERATOR}" "$base/api/v1/nodes"
echo
echo "health, 401, and node list succeeded"
