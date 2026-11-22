#!/bin/sh
# Build local worker and recipe images.
# Called from the node systemd unit ExecStartPre before the supervisor starts.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PARENT="$(cd "$SCRIPT_DIR/.." && pwd)"
if [ -f "$SCRIPT_DIR/worker-prep.Dockerfile" ]; then
  DEPLOY="$SCRIPT_DIR"
  CONTEXT="$SCRIPT_DIR"
elif [ -d "$PARENT/deploy" ]; then
  DEPLOY="$PARENT/deploy"
  CONTEXT="$PARENT"
else
  DEPLOY="$PARENT"
  CONTEXT="$PARENT"
fi

echo "Building audiobook-worker-prep:local..."
docker build -t audiobook-worker-prep:local -f "$DEPLOY/worker-prep.Dockerfile" "$CONTEXT"

echo "Building audiobook-worker-tts:local..."
docker build -t audiobook-worker-tts:local -f "$DEPLOY/worker-tts.Dockerfile" "$CONTEXT"

echo "Building audiobook-recipe-michael-cosyvoice:local..."
docker build -t audiobook-recipe-michael-cosyvoice:local -f "$DEPLOY/recipe-michael-cosyvoice.Dockerfile" "$CONTEXT"

echo "Building audiobook-recipe-ggardner-indextts:local..."
docker build -t audiobook-recipe-ggardner-indextts:local -f "$DEPLOY/recipe-ggardner-indextts.Dockerfile" "$CONTEXT"

echo "Building audiobook-probe:dev..."
docker build -t audiobook-probe:dev -f "$DEPLOY/probe.Dockerfile" "$CONTEXT"

echo "All images built."
