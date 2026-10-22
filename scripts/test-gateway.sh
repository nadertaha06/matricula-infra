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
# Responde com a porta e o caminho recebidos, alem do X-Service-Token que chegou;
# devolve o X-Request-ID recebido em um cabecalho proprio.
cat > "$work/stub/default.conf" <<'EOF'
server {
    listen 80; listen 3000; listen 8001; listen 8002; listen 8003;
    location /assets/ {
        default_type application/javascript;
        return 200 "ASSET_JS";
    }
    location / {
        add_header X-Recebido-Request-Id $http_x_request_id always;
        default_type text/plain;
        return 200 "$server_port $uri [$http_x_service_token]";
    }
}
EOF
# Bundle simulado com mais de 1 KB para a compressao do gateway.
sed -i.bak "s/ASSET_JS/$(printf 'console.log(1);%.0s' $(seq 1 200))/" "$work/stub/default.conf"
rm -f "$work/stub/default.conf.bak"
chmod -R a+rX "$work"

docker network create "$name" >/dev/null
docker run -d --name "$name-stub" --network "$name" \
  --network-alias matricula-frontend --network-alias matricula-disciplinas \
  --network-alias matricula-solicitacoes --network-alias matricula-processamento \
  --network-alias matricula-grafana \
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
expect 'bundle JS comprimido com gzip' 'gzip' \
  sh -c "curl -ksI -H 'Accept-Encoding: gzip' 'https://$https/assets/index-x.js' | tr -d '\r' | awk -F': ' 'tolower(\$1)==\"content-encoding\"{print \$2}'"
expect 'JSON da API sem compressao' '' \
  sh -c "curl -ksI -H 'Accept-Encoding: gzip' 'https://$https/api/disciplinas/ready' | tr -d '\r' | awk -F': ' 'tolower(\$1)==\"content-encoding\"{print \$2}'"
expect 'metricas dos servicos bloqueadas no gateway' '404' \
  curl -ks -o /dev/null -w '%{http_code}' "https://$https/api/disciplinas/metrics"
expect 'metricas bloqueadas com barra final e caixa alta' '404 404' \
  sh -c "echo \$(curl -ks -o /dev/null -w '%{http_code}' 'https://$https/api/processamento/metrics/') \$(curl -ks -o /dev/null -w '%{http_code}' 'https://$https/api/solicitacoes/METRICS')"
expect 'prefixo /api desconhecido' '404' curl -ks -o /dev/null -w '%{http_code}' "https://$https/api/outro/x"
expect 'Grafana pelo subcaminho' '3000 /grafana/d/matricula []' \
  curl -ks -H 'X-Service-Token: externo' "https://$https/grafana/d/matricula"
expect 'Grafana sem barra final' '308' curl -ks -o /dev/null -w '%{http_code}' "https://$https/grafana"
headers=$(curl -ksD - -o /dev/null "https://$https/api/disciplinas/ready" | tr -d '\r')
sent=$(printf '%s\n' "$headers" | awk -F': ' 'tolower($1)=="x-request-id"{print $2}')
received=$(printf '%s\n' "$headers" | awk -F': ' 'tolower($1)=="x-recebido-request-id"{print $2}')
expect 'X-Request-ID gerado e repassado ao servico' "$sent" printf '%s' "$received"
expect 'X-Request-ID com 32 caracteres' '32' printf '%s' "${#sent}"
expect 'status interno fora da porta publica' '0' \
  sh -c "curl -ks 'https://$https/stub_status' | grep -c 'Active connections' || true"
expect 'status interno disponivel na rede Docker' '1' \
  sh -c "docker exec '$name-gw' wget -qO- http://127.0.0.1:8081/stub_status | grep -c 'Active connections'"
codes=$(seq 1 400 | xargs -P 40 -I{} curl -ks -o /dev/null -w '%{http_code}\n' "https://$https/api/disciplinas/ready")
limited=$(printf '%s\n' "$codes" | grep -c '^429$' || true)
accepted=$(printf '%s\n' "$codes" | grep -c '^200$' || true)
expect 'rajada acima do limite recebe 429 e parte passa' 'sim' \
  sh -c "[ $limited -gt 0 ] && [ $accepted -ge 100 ] && echo sim || echo 'nao (429=$limited 200=$accepted)'"
echo "  rajada de 400: $accepted aceitas, $limited limitadas (429)"
echo 'Gateway HTTPS verificado.'
