#!/bin/bash
# Cria um banco por servico. O Postgres executa os scripts de
# /docker-entrypoint-initdb.d apenas na PRIMEIRA subida, com o volume vazio.
# Para reexecutar: docker compose down -v (isso apaga os dados).
set -e

for db in disciplinas_db solicitacoes_db processamento_db; do
  echo "Criando banco de dados: $db"
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -c "CREATE DATABASE $db;"
done

echo "Bancos criados."
