#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ACTION=${1:-status}
PROFILE=${2:-cpu}

case "$ACTION" in
    native)
        exec epsilon-flow-backend --host 127.0.0.1 --port 8791
        ;;
    start)
        docker compose -f "$ROOT/docker-compose.yml" --profile "$PROFILE" up -d "backend-$PROFILE"
        ;;
    stop)
        docker compose -f "$ROOT/docker-compose.yml" --profile "$PROFILE" down
        ;;
    logs)
        docker compose -f "$ROOT/docker-compose.yml" --profile "$PROFILE" logs -f "backend-$PROFILE"
        ;;
    status)
        docker compose -f "$ROOT/docker-compose.yml" ps
        ;;
    *)
        printf 'usage: %s {native|start|stop|logs|status} [cpu|cuda]\n' "$0" >&2
        exit 2
        ;;
esac
