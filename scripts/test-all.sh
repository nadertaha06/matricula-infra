#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
export DB_HOST=${DB_HOST:-localhost} DB_PORT=${DB_PORT:-5432}
export DB_USER=${DB_USER:-postgres} DB_PASSWORD=${DB_PASSWORD:-postgres}
export RABBITMQ_URL=${RABBITMQ_URL:-amqp://tests:tests@localhost:5672/etapa2-tests}
export WORKER_ENABLED=false
for service in disciplinas solicitacoes processamento; do
  (cd "$root/matricula-$service" && DB_NAME="${service}_test" python -m pytest)
done
