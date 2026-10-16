#!/usr/bin/env bash
# Sobe tls/default.conf com certificado temporario e servicos simulados; confere rotas e redirecionamentos.
set -Eeuo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
name=gateway-test-$$
cleanup() {
  docker rm -f "$name-gw" "$name-stub" >/dev/null 2>&1 || true
  docker network rm "$name" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

mkdir -p "$work/le/live/matricula-publico" "$work/acme/.well-known/acme-challenge" "$work/stub"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=gateway-test \
  -keyout "$work/le/live/matricula-publico/privkey.pem" \
  -out "$work/le/live/matricula-publico/fullchain.pem" 2>/dev/null
echo desafio > "$work/acme/.well-known/acme-challenge/token"
# Responde com a porta e o caminho recebidos, alem do X-Service-Token que chegou.
cat > "$work/stub/default.conf" <<'EOF'
server {
    listen 80; listen 8001; listen 8002; listen 8003;
    location / { default_type text/plain; return 200 "$server_port $uri [$http_x_service_token]"; }
}
EOF
chmod -R a+rX "$work"

docker network create "$name" >/dev/null
docker run -d --name "$name-stub" --network "$name" \
  --network-alias matricula-frontend --network-alias matricula-disciplinas \
  --network-alias matricula-solicitacoes --network-alias matricula-processamento \
  -v "$work/stub:/etc/nginx/conf.d:ro" nginx:1.28-alpine >/dev/null
docker run -d --name "$name-gw" --network "$name" \
  -p 127.0.0.1::80 -p 127.0.0.1::8080 \
  -v "$root/tls:/etc/nginx/conf.d:ro" \
  -v "$work/le:/etc/letsencrypt:ro" \
  -v "$work/acme:/var/www/acme:ro" nginx:1.28-alpine >/dev/null
http=$(docker port "$name-gw" 80/tcp | head -1)
https=$(docker port "$name-gw" 8080/tcp | head -1)

for _ in $(seq 1 20); do
  curl -kfs --max-time 2 "https://$https/health" >/dev/null && break
  sleep 1
done

expect() {
  local description=$1 wanted=$2 got
  shift 2
  got=$("$@")
  if [ "$got" != "$wanted" ]; then
    echo "FALHOU: $description: esperado [$wanted], obtido [$got]" >&2
    docker logs --tail 20 "$name-gw" >&2
    exit 1
  fi
  echo "ok: $description"
}
public=203.0.113.10

expect 'health HTTPS' 'ok' curl -ks "https://$https/health"
expect 'raiz vai para o frontend' '80 / []' curl -ks "https://$https/"
expect 'rotas do SPA vao para o frontend' '80 /matriculas []' curl -ks "https://$https/matriculas"
expect 'disciplinas sem o prefixo publico' '8001 /ready []' curl -ks "https://$https/api/disciplinas/ready"
expect 'solicitacoes sem o prefixo publico' '8002 /matriculas/1 []' curl -ks "https://$https/api/solicitacoes/matriculas/1"
expect 'processamento sem o prefixo publico' '8003 /docs []' curl -ks "https://$https/api/processamento/docs"
expect 'X-Service-Token externo removido' '8001 /turmas []' \
  curl -ks -H 'X-Service-Token: externo' "https://$https/api/disciplinas/turmas"
expect 'HTTP simples na 8080 vira HTTPS (POST incluido)' "308 https://$public:8080/api/disciplinas/turmas?x=1" \
  curl -s -o /dev/null -w '%{http_code} %{redirect_url}' -X POST -H "Host: $public:8080" "http://$https/api/disciplinas/turmas?x=1"
expect 'porta 80 redireciona para a 8080' "308 https://$public:8080/portal" \
  curl -s -o /dev/null -w '%{http_code} %{redirect_url}' -H "Host: $public" "http://$http/portal"
expect 'desafio ACME continua em HTTP' 'desafio' curl -s "http://$http/.well-known/acme-challenge/token"
expect 'prefixo antigo redireciona para /api' '308' \
  curl -ks -o /dev/null -w '%{http_code}' "https://$https/disciplinas/docs"
expect 'cabecalho nosniff' 'nosniff' \
  sh -c "curl -ksI 'https://$https/health' | tr -d '\r' | awk -F': ' 'tolower(\$1)==\"x-content-type-options\"{print \$2}'"
echo 'Gateway HTTPS verificado.'
