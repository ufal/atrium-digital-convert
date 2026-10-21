#!/usr/bin/env bash
# Start the ATRIUM digital-convert API server and wait until it is READY (strategy Appendix C).
#
# Behaviour:
#   1. GET <base-url>/ready answers 200 -> exit 0 (idempotent).
#   2. Otherwise start it: Docker Compose (`api` profile) by default, or uvicorn in the repository's
#      venv with --local. Docker first pulls the released image of this branch's version
#      (para_config.txt [tool] version); when it cannot, compose builds the image from this checkout.
#   3. Poll /ready for up to 15 minutes; on timeout say where the logs are and exit non-zero.
#
# Polls /ready, not /info (issue #55): /info answers as soon as the process is up, /ready only once
# the start-up checks have passed.
#
# There is no GPU mode: the converter uses no model.
#
# Usage:
#   bash scripts/server.sh            # Docker, or local uvicorn when Docker is not available
#   bash scripts/server.sh --local    # skip Docker, run the service in venv-dc
#
# Environment:
#   ATRIUM_DC_PORT  - port to serve on (default: 8000)
#   ATRIUM_DC_URL   - health-check target (default: http://localhost:$ATRIUM_DC_PORT)
#   ATRIUM_VERSION  - image tag to run (default: this branch's release, e.g. 1.2.1-beta)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${ATRIUM_DC_PORT:-8000}"
BASE_URL="${ATRIUM_DC_URL:-http://localhost:${PORT}}"
HEALTH_URL="${BASE_URL}/ready"
MODE="auto"

for arg in "$@"; do
    case "$arg" in
        --local) MODE="local" ;;
        --gpu) echo "digital-convert uses no model, so there is no GPU mode." >&2; exit 1 ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

if curl -sf "$HEALTH_URL" > /dev/null 2>&1; then
    echo "✅ API already ready at ${BASE_URL}"
    exit 0
fi

cd "$REPO_ROOT"

start_docker() {
    # The image tag is the release this branch was derived from, without the leading "v".
    ATRIUM_VERSION="${ATRIUM_VERSION:-$(sed -n 's/^version *= *v\{0,1\}//p' para_config.txt | head -1)}"
    export ATRIUM_VERSION PORT
    echo "🐳 Starting digital-convert ${ATRIUM_VERSION} via docker compose (api profile)..."
    docker compose --profile api pull digital-convert-api \
        || echo "Could not pull the ${ATRIUM_VERSION} image; building it from this checkout."
    docker compose --profile api up -d digital-convert-api
}

start_local() {
    echo "🐍 Starting the service locally..."
    if [ ! -d "venv-dc" ]; then
        echo "No venv found - creating venv-dc and installing service/requirements.txt..."
        python3 -m venv venv-dc
        # shellcheck disable=SC1091
        source venv-dc/bin/activate
        pip install --upgrade pip
        pip install -r service/requirements.txt
    else
        # shellcheck disable=SC1091
        source venv-dc/bin/activate
    fi
    # DOC and XLS need LibreOffice (`soffice`) on the PATH; without it they answer 501.
    PORT="$PORT" nohup python -m service.api > api_server.log 2>&1 &
    echo "Server PID: $! (logs: api_server.log)"
}

case "$MODE" in
    local) start_local ;;
    auto)
        if command -v docker > /dev/null 2>&1 && docker info > /dev/null 2>&1; then
            start_docker
        else
            start_local
        fi
        ;;
esac

echo "⏳ Waiting for ${HEALTH_URL} (a first image pull or build can take several minutes)..."
DEADLINE=$((SECONDS + 900))
until curl -sf "$HEALTH_URL" > /dev/null 2>&1; do
    if [ "$SECONDS" -ge "$DEADLINE" ]; then
        echo "❌ Server did not become ready within 15 minutes." >&2
        echo "   Check: api_server.log (local) or 'docker compose --profile api logs digital-convert-api' (Docker)." >&2
        exit 1
    fi
    sleep 5
done

echo "✅ API ready at ${BASE_URL}"
