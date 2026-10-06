#!/bin/bash
# Tests -> Build -> Deploy -> Verify. Aborts on the first failure and leaves
# the running app untouched. Runs interactively or from the deploy pipeline.

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[$(date '+%H:%M:%S')] $*${NC}"; }
warn() { echo -e "${YELLOW}[$(date '+%H:%M:%S')] $*${NC}"; }
err()  { echo -e "${RED}[$(date '+%H:%M:%S')] $*${NC}" >&2; }

HEALTH_TIMEOUT=60
HEALTH_INTERVAL=2

warn "--- Redeploy Script ---"

# Ensure containers run as current user
export HOST_UID=$(id -u)
export HOST_GID=$(id -g)

# --- STEP 0: PRE-FLIGHT TESTS ---
TIMESTAMP=$(date +%s)
TEST_CONTAINER_NAME="test_run_${TIMESTAMP}"
REBUILT=0

log "0. Running tests (testmon: only affected tests)..."
echo "   Container name: ${TEST_CONTAINER_NAME}"

# Only rebuild image if requirements.txt changed (source comes via volume mount)
REQ_HASH_FILE="app/.requirements_hash"
REQ_HASH_CURRENT=$(md5sum app/requirements.txt | cut -d' ' -f1)
REQ_HASH_SAVED=$(cat "$REQ_HASH_FILE" 2>/dev/null || echo "")

if [ "$REQ_HASH_CURRENT" != "$REQ_HASH_SAVED" ]; then
    warn "   requirements.txt changed -> rebuilding app-tests image..."
    docker compose build app-tests
    echo "$REQ_HASH_CURRENT" > "$REQ_HASH_FILE"
    REBUILT=1
fi

docker compose run --name "$TEST_CONTAINER_NAME" --rm app-tests pytest --testmon
TEST_EXIT_CODE=$?

if [ $REBUILT -ne 0 ]; then
    log "Restarting persistent test container with new image."
    docker compose down app-tests
    docker compose up -d app-tests
fi

if [ $TEST_EXIT_CODE -ne 0 ]; then
    err "TESTS FAILED! (Exit code: $TEST_EXIT_CODE)"
    err "Deployment aborted. The running app is unchanged."
    exit 1
fi
log "Tests passed. Starting deployment..."

# --- STEPS 1-5: DEPLOYMENT (Build BEFORE Stop for minimal downtime) ---

export APP_VERSION=$(cat VERSION 2>/dev/null || echo "0.0")
export GIT_COMMIT=$(git rev-parse --short HEAD)
export BUILD_TIME=$(date -Iseconds)
log "Build: v${APP_VERSION} (${GIT_COMMIT}) @ ${BUILD_TIME}"

# 1. Build new image WHILE app is still running (= no downtime during build)
log "1. Building new image (app keeps running)..."
docker compose build --no-cache app
BUILD_EXIT_CODE=$?
if [ $BUILD_EXIT_CODE -ne 0 ]; then
    err "BUILD FAILED! (Exit code: $BUILD_EXIT_CODE)"
    err "Deployment aborted. App continues running with old image."
    exit 1
fi

# 2. Stop the service (image is already built -> short downtime)
log "2. Stopping service 'app'..."
docker compose stop app

# 3. Remove the container
log "3. Removing container 'app'..."
docker compose rm -f app

# 4. Check for orphaned containers
log "4. Checking for orphaned containers..."
PROJECT_NAME=$(grep -E '^COMPOSE_PROJECT_NAME=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')
PROJECT_NAME=${PROJECT_NAME:-$(basename "$(pwd)" | tr '[:upper:]' '[:lower:]')}
ORPHANS=$(docker ps -aq \
  --filter "label=com.docker.compose.project=${PROJECT_NAME}" \
  --filter "label=com.docker.compose.service=app")

if [ -n "$ORPHANS" ]; then
    warn "Orphaned containers found. Force removing:"
    echo "$ORPHANS"
    docker rm -f $ORPHANS
else
    echo "No orphaned containers found. All clean."
fi

# 5. Start container with new image (db-backup is started alongside if missing)
log "5. Starting container with new image..."
docker compose up -d app db-backup

# 6. Verify: the running container must be healthy AND run the commit just built.
#    A container that came up from a stale image is a hard failure, not a warning.
log "6. Verifying deployment (expecting ${GIT_COMMIT})..."
elapsed=0
status="unknown"
while [ $elapsed -lt $HEALTH_TIMEOUT ]; do
    status=$(docker compose ps --format '{{.Health}}' app 2>/dev/null || echo "unknown")
    [ "$status" = "healthy" ] && break
    sleep $HEALTH_INTERVAL
    elapsed=$((elapsed + HEALTH_INTERVAL))
done
if [ "$status" != "healthy" ]; then
    err "App did NOT become healthy within ${HEALTH_TIMEOUT}s (status: ${status})."
    docker compose logs --tail=50 app
    exit 1
fi
RUNNING_COMMIT=$(docker compose exec -T app printenv GIT_COMMIT 2>/dev/null | tr -d '\r')
if [ "$RUNNING_COMMIT" != "$GIT_COMMIT" ]; then
    err "App runs '${RUNNING_COMMIT:-<none>}', expected '${GIT_COMMIT}'. Image was NOT rebuilt."
    exit 1
fi
log "Deployed v${APP_VERSION} (${GIT_COMMIT}), app is healthy."

# 7. Follow logs only when attached to a terminal (the deploy pipeline is not)
if [ -t 1 ]; then
    log "7. Showing logs (press Ctrl+C to exit):"
    docker compose logs -f app 2>&1
fi
