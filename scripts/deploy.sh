#!/usr/bin/env bash
# Executado na EC2 pelo CI: aplica o gateway HTTPS do commit aprovado e confere a aplicacao inteira.
set -Eeuo pipefail
sha=${1:?Informe o commit aprovado}
repo=${INFRA_DIR:-$HOME/matricula-infra}
gateway=matricula-https
apps=(postgres rabbitmq matricula-disciplinas matricula-solicitacoes matricula-processamento matricula-frontend)
units=(matricula-certbot.service matricula-certbot.timer)
install -m 700 -d "$HOME/.config/matricula"
# Mesmo lock dos deploys dos servicos: um deploy por vez na EC2.
exec 9>"$HOME/.config/matricula/deploy.lock"
flock -w 300 9

# Os containers da aplicacao sao implantados pelos pipelines de cada servico.
# Aqui eles so sao religados se estiverem parados; nada e alterado se algum faltar.
for app in "${apps[@]}"; do
  state=$(docker inspect --format '{{.State.Status}}' "$app" 2>/dev/null || echo ausente)
  case "$state" in
    running) ;;
    ausente) echo "$app nao existe; execute o pipeline do servico antes da infra." >&2; exit 1 ;;
    *) echo "Religando $app ($state)"; docker start "$app" >/dev/null ;;
  esac
done

cd "$repo"
previous=$(git rev-parse HEAD)
backup=$(mktemp -d)
cp -a compose.https.yml compose.proxy.yml tls proxy "$backup"/ 2>/dev/null || true

rollback() {
  trap - ERR
  set +e
  echo "Deploy falhou; restaurando configuracao anterior ($previous)." >&2
  docker logs --tail 30 "$gateway" 2>&1
  git reset -q --hard "$previous"
  cp -a "$backup"/. .
  if [ -f compose.https.yml ]; then
    docker compose -f compose.https.yml up -d --wait && docker exec "$gateway" nginx -s reload
  fi
  if ! docker ps --format '{{.Ports}}' | grep -q ':8080->'; then
    docker compose -f compose.proxy.yml up -d --wait
  fi
  exit 1
}
trap rollback ERR

git fetch -q origin main
# reset --hard tambem substitui copias manuais nao rastreadas dos mesmos arquivos.
git reset -q --hard "$sha"

docker run --rm --network rede \
  -v "$repo/tls:/etc/nginx/conf.d:ro" \
  -v /etc/letsencrypt:/etc/letsencrypt:ro \
  -v /var/www/matricula-acme:/var/www/acme:ro \
  nginx:1.28-alpine nginx -t

# Renovacao do certificado de IP (dura seis dias) pela porta 80.
sudo install -d -m 755 /var/www/matricula-acme
for unit in "${units[@]}"; do sudo install -m 644 "tls/$unit" "/etc/systemd/system/$unit"; done
sudo systemctl daemon-reload
sudo systemctl enable --now matricula-certbot.timer

# O gateway HTTPS assume a porta 8080 do proxy HTTP antigo.
if docker inspect matricula-proxy >/dev/null 2>&1; then docker rm -f matricula-proxy >/dev/null; fi
docker compose -f compose.https.yml up -d --wait
docker exec "$gateway" nginx -s reload

# -k: o certificado e do IP publico; o CI confere a cadeia por fora apos o deploy.
healthy=
for _ in $(seq 1 30); do
  if curl -kfsS --max-time 3 https://127.0.0.1:8080/health >/dev/null &&
     curl -kfsS --max-time 3 https://127.0.0.1:8080/ | grep 'id="root"' >/dev/null &&
     curl -kfsS --max-time 3 https://127.0.0.1:8080/api/disciplinas/ready >/dev/null &&
     curl -kfsS --max-time 3 https://127.0.0.1:8080/api/solicitacoes/ready >/dev/null &&
     curl -kfsS --max-time 3 https://127.0.0.1:8080/api/processamento/ready >/dev/null; then
    healthy=1
    break
  fi
  sleep 2
done
test -n "$healthy"
# O desafio ACME precisa continuar em HTTP simples (404 aqui, nunca redirecionamento).
test "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/.well-known/acme-challenge/verificacao || true)" = 404

trap - ERR
rm -rf "$backup"
echo "Gateway implantado em $sha; portal e tres servicos respondendo em HTTPS."
