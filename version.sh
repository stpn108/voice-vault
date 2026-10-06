#!/bin/bash
# Shows repo version and compares with running app

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# --- Repo ---
REPO_VERSION=$(cat VERSION 2>/dev/null || echo "?")
REPO_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "?")

echo -e "${YELLOW}Repo:${NC}  v${REPO_VERSION} (${REPO_COMMIT})"

# --- Running App ---
APP_VERSION=$(docker compose exec -T app printenv APP_VERSION 2>/dev/null | tr -d '\r')
APP_COMMIT=$(docker compose exec -T app printenv GIT_COMMIT 2>/dev/null | tr -d '\r')
APP_BUILD=$(docker compose exec -T app printenv BUILD_TIME 2>/dev/null | tr -d '\r')

if [ -z "$APP_VERSION" ]; then
    echo -e "${RED}App:${NC}   not reachable (container not running?)"
    exit 1
fi

echo -e "${YELLOW}App:${NC}   v${APP_VERSION} (${APP_COMMIT}) built ${APP_BUILD}"

# --- Compare ---
if [ "$REPO_VERSION" = "$APP_VERSION" ] && [ "$REPO_COMMIT" = "$APP_COMMIT" ]; then
    echo -e "${GREEN}Repo and app are identical.${NC}"
elif [ "$REPO_VERSION" = "$APP_VERSION" ]; then
    echo -e "${YELLOW}~ Same version, different commit. Redeploy needed.${NC}"
else
    echo -e "${RED}Version mismatch! Repo v${REPO_VERSION} != App v${APP_VERSION}${NC}"
fi
