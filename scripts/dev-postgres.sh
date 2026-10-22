#!/usr/bin/env bash
# Fallback for machines without Docker: run a local Postgres 16 (+pgvector from apt)
# with the same user/db/credentials as docker-compose.yml. Usage: scripts/dev-postgres.sh [start|stop]
set -euo pipefail
BIN=/usr/lib/postgresql/16/bin
DIR="${FINRAG_PGDATA:-/var/tmp/finrag-pgdata}"
RUN_AS=()
if [ "$(id -u)" = 0 ]; then
  id postgres >/dev/null 2>&1 && RUN_AS=(su postgres -c)
fi
run() { if [ ${#RUN_AS[@]} -gt 0 ]; then "${RUN_AS[@]}" "$*"; else bash -c "$*"; fi; }

case "${1:-start}" in
  start)
    if [ ! -d "$DIR" ]; then
      mkdir -p "$DIR"; [ ${#RUN_AS[@]} -gt 0 ] && chown postgres "$DIR"
      run "$BIN/initdb -D $DIR -A trust >/dev/null"
    fi
    run "$BIN/pg_ctl -D $DIR -l $DIR/log -o '-p 5432 -k /tmp' -w start" || true
    psql -h /tmp -U postgres -tc "SELECT 1 FROM pg_roles WHERE rolname='finrag'" | grep -q 1 \
      || psql -h /tmp -U postgres -c "CREATE ROLE finrag SUPERUSER LOGIN PASSWORD 'finrag'" >/dev/null
    psql -h /tmp -U postgres -tc "SELECT 1 FROM pg_database WHERE datname='finrag'" | grep -q 1 \
      || psql -h /tmp -U postgres -c "CREATE DATABASE finrag OWNER finrag" >/dev/null
    echo "postgres ready on localhost:5432 (finrag/finrag)";;
  stop) run "$BIN/pg_ctl -D $DIR -m fast stop";;
esac
