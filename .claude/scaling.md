# Scaling to Several App Instances (when needed)

**The default is one `app` service.** Do not add replicas, a load
balancer, Redis or a connection pooler to a project that has not measured
a need. This file describes the target layout and the prerequisites so
that, when the need arrives, the change is a known path and not a
redesign. Distilled from ZenTallyBot (plan-horizontal-scaling, D-064,
D-066, D-069, D-080, D-083, D-110).

## When

Scale when one of these is measured, not guessed:

- The health check or latency degrades under real load and a bigger
  `mem_limit` does not help.
- A deploy's downtime (stop → start of the single container) is no longer
  acceptable for users.
- One instance crashing must not take the product offline.

## Naming

| Role | Service name | Notes |
|------|--------------|-------|
| App instance | `app-1`, `app-2`, `app-3` | Explicit services, one per instance. Never `deploy.replicas` (see below). Never `bot-1` / `api-1`: the template has one app; a second kind of process gets its own name and its own section here. |
| Load balancer | `haproxy` | The only service that binds host ports. Takes over `127.0.0.1:${PORTS_PREFIX}010`, the port `app` used before. |
| Shared state | `redis` | Only if the app holds state in memory that all instances must see. |
| DB pooling | `pgbouncer` | Only when `instances × pool_size` exceeds what Postgres should hold. |
| Scheduled jobs | `scheduler` | One instance of the app image that runs jobs and serves no traffic. |

Explicit `app-N` services instead of `deploy.replicas`: Compose recreates
all replicas at once on a new image, so zero-downtime deploys are
impossible with replicas. Explicit services can be replaced one at a time
(ZenTallyBot D-064, D-069).

## Prerequisites in the code (do these first, still with one instance)

1. **No in-memory state that matters across requests.** Caches,
   rate-limit counters, debounce buffers, "already sent" sets: move to
   Redis or the database, or accept that they are per-instance. Config
   caches may stay in memory with a TTL (60 s is fine).
2. **Scheduled jobs run once.** Every job wrapped in a leader lock
   (`SET lock:<job> <instance> NX EX <ttl>` in Redis; fail-open with a
   warning if Redis is down) or moved to the single `scheduler` service.
3. **Schema setup is concurrent-safe.** Already true: `migrate_schema()`
   holds a Postgres advisory lock (T-002).
4. **HTTP health endpoint.** `GET /health` returning 200 only when DB
   (and Redis, if used) answer; HAProxy needs it. The Docker healthcheck
   can stay `healthcheck.py`.
5. **Graceful shutdown.** On SIGTERM finish in-flight requests, then exit;
   HAProxy drains the instance during the deploy.
6. **Unit tests do not need Redis.** An autouse fixture in `conftest.py`
   makes the Redis client raise so locks fail open in tests (ZenTallyBot
   D-123).

## Target Compose layout

```yaml
x-app-common: &app-common
  build:
    context: ./app
    args: { APP_VERSION: ${APP_VERSION:-0.0}, GIT_COMMIT: ${GIT_COMMIT:-unknown}, BUILD_TIME: ${BUILD_TIME:-unknown} }
  environment:
    DATABASE_URL: ${DATABASE_URL}        # or the pgbouncer URL
    REDIS_URL: redis://redis:6379/0
    TZ: ${TZ:-Europe/Berlin}
    LOG_LEVEL: ${LOG_LEVEL:-INFO}
  logging: *default-logging
  mem_limit: 512m
  memswap_limit: 512m
  user: "${HOST_UID:-1000}:${HOST_GID:-1000}"
  healthcheck:
    test: ["CMD-SHELL", "curl -fs http://localhost:8000/health || exit 1"]
    interval: 10s
    timeout: 5s
    start_period: 20s
    retries: 3
  depends_on:
    db: { condition: service_healthy }
    redis: { condition: service_healthy }
  restart: unless-stopped

services:
  app-1: { <<: *app-common }
  app-2: { <<: *app-common }
  app-3: { <<: *app-common }

  scheduler:
    <<: *app-common
    command: ["python", "scheduler.py"]   # jobs only, no HTTP
    healthcheck: { test: ["CMD", "python", "healthcheck.py"], interval: 30s, timeout: 10s, retries: 3 }

  haproxy:
    image: haproxy:3.0-alpine
    logging: *default-logging
    mem_limit: 64m
    memswap_limit: 64m
    volumes:
      - ./haproxy/haproxy.cfg:/usr/local/etc/haproxy/haproxy.cfg:ro
    ports:
      - "127.0.0.1:${PORTS_PREFIX}010:8000"   # the port app used to bind
      - "127.0.0.1:${PORTS_PREFIX}404:8404"   # stats
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://127.0.0.1:8404/stats || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 3
    restart: unless-stopped
    # No depends_on on app-N: a force-recreate of one instance would restart
    # HAProxy and drop every connection (ZenTallyBot D-080). HAProxy's own
    # checks mark instances up/down.

  redis:
    image: redis:7.4-alpine
    logging: *default-logging
    mem_limit: 192m
    memswap_limit: 192m
    command: ["redis-server", "--maxmemory", "128mb", "--maxmemory-policy", "allkeys-lru", "--save", "", "--appendonly", "no"]
    healthcheck: { test: ["CMD", "redis-cli", "ping"], interval: 5s, timeout: 3s, retries: 5 }
    restart: unless-stopped
```

`app-N` services bind no host ports. `ports:` on `app` is removed; HAProxy
owns `${PORTS_PREFIX}010`. Everything the reverse proxy on the host pointed
at keeps working unchanged.

## HAProxy config (`haproxy/haproxy.cfg`)

```
global
    log stdout format raw local0
    maxconn 1024

defaults
    log     global
    mode    http
    option  httplog
    option  dontlognull
    timeout connect 5s
    timeout client  60s
    timeout server  60s      # raise for long LLM calls
    timeout http-request 10s

frontend app
    bind *:8000
    default_backend app-servers

backend app-servers
    option httpchk GET /health
    http-check expect status 200
    default-server inter 5s fall 3 rise 2
    server app-1 app-1:8000 check
    server app-2 app-2:8000 check
    server app-3 app-3:8000 check

frontend stats
    bind *:8404
    stats enable
    stats uri /stats
    stats refresh 10s
```

Explicit `server` lines, no `server-template`, no DNS resolvers: the
instances are fixed services, and this stays readable.

## Rolling deploy in `redeploy.sh`

Replace the single stop/rm/up block with, per instance:

```bash
DEPLOY_SERVICES="app-1 app-2 app-3 scheduler"
docker compose build $DEPLOY_SERVICES            # once, before touching anything
for svc in $DEPLOY_SERVICES; do
    docker compose up -d --no-deps --force-recreate "$svc"
    wait_healthy "$svc" || exit 1                # 60 s, poll docker compose ps
done
verify_commit $DEPLOY_SERVICES                   # GIT_COMMIT == built, else exit 1
```

At least N-1 instances serve traffic at all times; HAProxy marks the
recreating one down within `fall × inter` seconds and up again after
`rise × inter`. Keep the build list and the verify list as one variable
(ZenTallyBot D-156: a service missing from the build list ran a stale image
for weeks).

## What does not change

- Tests, the `app-tests` service, the deploy pipeline and the PR comment.
- `db`, `db-backup`, `.env` keys except the added `REDIS_URL`.
- `migrate_schema()`: every instance calls it; the advisory lock makes all
  but one wait.
- Memory limits on every service, including the new ones (ZenTallyBot
  D-083: one unlimited container took the host down).

## Beyond one server

Cloud (managed containers, Kubernetes) is a separate decision. Before it:
no file-system state (move it to the DB or object storage), secrets in the
platform's store, and the same health endpoint. Nothing in this template
assumes a single host except the runner and `volumes/`.
