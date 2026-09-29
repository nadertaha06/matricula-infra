#!/usr/bin/env bash
set -euo pipefail
for database in disciplinas_test solicitacoes_test processamento_test; do
  createdb --username "$POSTGRES_USER" "$database"
done
