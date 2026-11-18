#!/bin/sh
# Build local worker and recipe images.
# Called from the node systemd unit ExecStartPre before the supervisor starts.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "Building audiobook-worker-prep:local..."
docker build -t audiobook-worker-prep:local -f "$ROOT/deploy/worker-prep.Dockerfile" "$ROOT"

echo "Building audiobook-worker-tts:local..."
docker build -t audiobook-worker-tts:local -f "$ROOT/deploy/worker-tts.Dockerfile" "$ROOT"

echo "Building audiobook-recipe-michael:local..."
docker build -t audiobook-recipe-michael:local -f "$ROOT/deploy/recipe-michael.Dockerfile" "$ROOT"

echo "Building audiobook-probe:dev..."
docker build -t audiobook-probe:dev -f "$ROOT/deploy/probe.Dockerfile" "$ROOT"

echo "All images built."
