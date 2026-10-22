# Sistema de matricula em disciplinas

Infraestrutura do sistema de matrícula: API Gateway HTTPS, Prometheus, Grafana, teste de carga e deploy automático.
A aplicacao usa tres servicos Python/FastAPI com PostgreSQL, comunicacao HTTP e RabbitMQ, e um portal React com login Auth0.

**Portal:** https://13.220.42.157:8080/ · **Grafana:** https://13.220.42.157:8080/grafana/ (leitura sem login)

| Repositorio | Responsabilidade | Porta | Banco |
|---|---|---:|---|
| [matricula-disciplinas](https://github.com/nadertaha06/matricula-disciplinas) | Oferta, horarios e reservas de vagas | 8001 | disciplinas_db |
| [matricula-solicitacoes](https://github.com/nadertaha06/matricula-solicitacoes) | Solicitacoes, historico e estados | 8002 | solicitacoes_db |
| [matricula-processamento](https://github.com/nadertaha06/matricula-processamento) | Regras e lista de espera | 8003 | processamento_db |
| [matricula-frontend](https://github.com/nadertaha06/matricula-frontend) | Portal React com Auth0 | 80 (interno) | — |

## Arquitetura implementada

```mermaid
flowchart LR
    U[Navegador] -->|HTTPS :8080| G[API Gateway Nginx]
    U -->|login OAuth2 + PKCE| A[Auth0]
    G --> F[Portal React]
    G -->|/api/disciplinas| D[Disciplinas :8001]
    G -->|/api/solicitacoes| S[Solicitacoes :8002]
    G -->|/api/processamento| P[Processamento :8003]
    G -->|/grafana| GR[Grafana]
    GR --> PR[Prometheus]
    PR -. coleta /metrics .-> D
    PR -. coleta .-> S
    PR -. coleta .-> P
    PR -. coleta .-> G
    PR -. coleta .-> R
    D --> DB1[(disciplinas_db)]
    S --> DB2[(solicitacoes_db)]
    P --> DB3[(processamento_db)]
    S -->|solicitada / cancelada| R[RabbitMQ]
    D -->|vaga.liberada| R
    R --> P
    P -->|HTTP oferta / reserva| D
    P -->|HTTP solicitacao / historico| S
    P -->|avaliada / lista.reavaliar| R
    R --> S
```

Os bancos sao logicamente separados numa unica instancia PostgreSQL. Nenhum servico le tabelas de outro banco.
Na EC2, cada servico possui usuario proprio de banco e configuracao privada.

## Executar localmente

Clone os quatro repositorios como diretorios irmaos:

```sh
git clone https://github.com/nadertaha06/matricula-infra.git
git clone https://github.com/nadertaha06/matricula-disciplinas.git
git clone https://github.com/nadertaha06/matricula-solicitacoes.git
git clone https://github.com/nadertaha06/matricula-processamento.git
cd matricula-infra
cp .env.example .env
# Edite .env com senhas locais; nao versione esse arquivo.
python3 scripts/configure.py
docker network inspect rede >/dev/null 2>&1 || docker network create rede
docker compose up -d --build --wait
python3 scripts/smoke_e2e.py
```

Requisitos: Python 3.12 para os servicos, Python 3 para os scripts de demonstracao, Docker e Compose v2 com `--wait`.
O teste E2E retorna `resultado: PASS` somente apos observar os resultados assincronos esperados.
Cada execucao cria registros sinteticos com identificador unico; nao apaga registros anteriores.

## Acesso publico

Entrada unica: **https://13.220.42.157:8080/** (portal com login Auth0)

- [Disciplinas](https://13.220.42.157:8080/api/disciplinas/docs)
- [Solicitacoes](https://13.220.42.157:8080/api/solicitacoes/docs)
- [Processamento](https://13.220.42.157:8080/api/processamento/docs)

Links antigos `http://13.220.42.157:8080/...` e `http://13.220.42.157/...` sao redirecionados para HTTPS. O gateway HTTPS (`compose.https.yml`, secao abaixo) encaminha a raiz para o portal e cada prefixo `/api/{servico}` para o servico correspondente na rede Docker. As portas 8001-8003 continuam internas. O container antigo `avaliacao_api` foi parado e seu reinicio automatico desativado.

O proxy HTTP original (`compose.proxy.yml`) continua disponivel para ambientes sem certificado. Ele tambem usa a porta 8080, portanto execute apenas um dos dois:

```sh
sudo ufw allow 8080/tcp
# Configure ROOT_PATH=/api/disciplinas, /api/solicitacoes e /api/processamento
# nos respectivos arquivos privados de runtime e reimplante os servicos.
docker compose -f compose.proxy.yml up -d --wait
python3 scripts/smoke_e2e.py --base-url http://IP_PUBLICO:8080
```

Para uma instalacao local nova, `scripts/configure.py` ja gera ROOT_PATH. Suba a aplicacao completa e depois o compose do proxy. Nginx resolve novamente os nomes Docker durante a execucao, acompanhando a troca de IP dos containers em futuros deploys.

## Demonstracao da entrega

Acesse `/docs` nas portas 8001, 8002 e 8003. Para testar a implantacao publicada:

```sh
python3 scripts/smoke_e2e.py --base-url https://13.220.42.157:8080
```

Se o Security Group nao liberar as portas 8001-8003, abra um tunel e use `http://localhost:8001/docs`, `http://localhost:8002/docs` e `http://localhost:8003/docs`:

```sh
ssh -i ../projsoft26b.pem -N \
  -L 8001:127.0.0.1:8001 \
  -L 8002:127.0.0.1:8002 \
  -L 8003:127.0.0.1:8003 ubuntu@13.220.42.157
# Em outro terminal:
python3 scripts/smoke_e2e.py --host 127.0.0.1
```

Tambem e possivel executar o roteiro diretamente na EC2:

```sh
ssh -i ../projsoft26b.pem ubuntu@13.220.42.157 \
  'python3 ~/matricula-infra/scripts/smoke_e2e.py'
```

O roteiro exercita:

1. Prontidao dos tres bancos e servicos.
2. Cadastro de disciplinas, turma com uma vaga e historicos ficticios.
3. Deferimento assincrono e repeticao idempotente da solicitacao.
4. Lista de espera ordenada por coeficiente.
5. Indeferimento por pre-requisito e choque de horario.
6. Cancelamento, liberacao de vaga e promocao automatica em duas rodadas.
7. Conferencia da capacidade e da trilha de estados.

Para conferir o resultado manualmente, use os IDs retornados pelo script em `GET /matriculas/{id}`, `GET /avaliacoes/{id}` e `GET /turmas/{id}`.
O RabbitMQ fica na rede Docker; o painel administrativo local e acessado por tunel SSH, sem necessidade de expor AMQP na internet:

```sh
ssh -i /caminho/da/chave.pem -L 15672:localhost:15672 ubuntu@13.220.42.157
```

## Testes reais e relatorios

Suba as dependencias **exclusivas de testes** (as portas 5432 e 5672 locais devem estar livres):

```sh
docker compose -f compose.test.yml up -d --wait
```

Em um ambiente Python 3.12 ativado, instale `requirements-dev.txt` dos servicos e execute:

```sh
bash scripts/test-all.sh
```

Os testes usam bancos terminados em `_test` e o vhost `etapa2-tests`. Testes unitarios isolam regras; testes de integracao usam PostgreSQL e RabbitMQ reais. Os testes do cliente HTTP isolam erros de protocolo; `smoke_e2e.py` verifica a comunicacao real entre todas as APIs.

Cada repositorio aplica minimo de **80% de cobertura de linhas e branches** via pytest-cov/coverage.py. O GitHub Actions publica o percentual medido no resumo e disponibiliza HTML, XML, JSON e JUnit como artefatos. Relatorios gerados nao ficam no Git.

- [CI disciplinas](https://github.com/nadertaha06/matricula-disciplinas/actions)
- [CI solicitacoes](https://github.com/nadertaha06/matricula-solicitacoes/actions)
- [CI processamento](https://github.com/nadertaha06/matricula-processamento/actions)

Abra o artefato da execucao desejada e, apos extrair, abra `coverage/index.html`. O relatorio permite navegar por arquivo e ver linhas e branches nao exercitados.

## Deploy automatico

Os tres repositorios possuem pipelines independentes: testes -> imagem DockerHub com tag SHA -> SSH -> readiness -> rollback em caso de falha.
A EC2 usa a rede `rede`, PostgreSQL e RabbitMQ compartilhados pela aplicacao. O deploy dos servicos nao recria os bancos nem os volumes.

Secrets dos repositorios de servico: `DOCKERHUB_TOKEN`, `HOST_TEST` e `KEY_TEST`.
Variavel: `EC2_HOST_KEY`, contendo a chave publica SSH esperada.
Arquivos privados na EC2: `~/.config/matricula/matricula-{servico}.env`, modo 600. Podem ser preparados a partir de `runtime/*.env`, ajustando usuarios de banco e credenciais do ambiente.

`DB_PASSWORD`, `RABBITMQ_URL` e campos Auth0 anteriormente criados no GitHub nao sao interpolados no shell de deploy. A configuracao efetiva vem dos arquivos privados da EC2.
A imagem implantada e a do SHA aprovado nos testes, mesmo que outro push atualize `latest`.
Um lock no servidor serializa as atualizacoes para reduzir o uso simultaneo de disco e memoria.

O compose completo e voltado ao primeiro provisionamento/local. Em uma EC2 existente, preserve o volume do PostgreSQL e o volume/nodename do RabbitMQ antes de alterar sua composicao. **Nao execute `down -v` para atualizar a aplicacao.**

### Deploy automatico da infra

[CI infra](https://github.com/nadertaha06/matricula-infra/actions): cada push na `main` testa `scripts/deploy.sh` (deploy e rollback com comandos simulados), valida os arquivos compose e sobe `tls/default.conf` com certificado temporario e servicos simulados (`scripts/test-gateway.sh`), conferindo rotas, redirecionamentos HTTP→HTTPS, desafio ACME e remocao de `X-Service-Token`. Pull requests executam somente essa validacao.

Aprovado o commit, o pipeline executa `scripts/deploy.sh` na EC2 por SSH, com o mesmo lock dos servicos:

1. Religa containers parados da aplicacao (PostgreSQL, RabbitMQ, tres APIs e frontend). Se algum nao existir, para sem alterar nada: ele e criado pelo pipeline do proprio servico.
2. Atualiza `~/matricula-infra` para o SHA aprovado e valida o Nginx com o certificado real.
3. Instala as unidades de renovacao do certificado e ativa o timer.
4. Substitui o proxy HTTP antigo pelo gateway HTTPS e recarrega a configuracao.
5. Confere portal, `/api/{servico}/ready` dos tres servicos pela 8080 e o desafio ACME em HTTP. Em falha, restaura o commit e os arquivos anteriores; se a 8080 ficar livre, religa o proxy HTTP.

Por fim, o runner acessa `https://IP:8080` de fora, validando o certificado publico, o Security Group e o redirecionamento de HTTP. Usa os mesmos secrets `HOST_TEST` e `KEY_TEST` e a variavel `EC2_HOST_KEY` dos servicos.

## Etapa 4: API Gateway, monitoramento e teste de carga

### API Gateway

`tls/default.conf` (container `matricula-https`) e a unica entrada publica. Alem de TLS e do roteamento por prefixo, ele:

| Responsabilidade | Implementacao |
|---|---|
| Limite de taxa por cliente | `limit_req` de 50 req/s por IP em `/api/` (rajada de 100) e 20 req/s no Grafana; o excesso recebe **429** sem chegar aos servicos |
| Correlacao | gera `X-Request-ID`, repassa aos servicos e devolve ao cliente |
| Credenciais internas | remove `X-Service-Token` de toda requisicao externa (lista unica em `tls/proxy-headers.inc`) |
| Metricas internas | `/api/{servico}/metrics` responde 404 por fora; o Prometheus coleta pela rede Docker |
| Observabilidade | log de acesso em JSON com tempo total e tempo do servico; `stub_status` na porta interna 8081 |
| Desempenho | gzip para o bundle do portal (JS/CSS/SVG); respostas JSON da API ficam sem compressao |
| Compatibilidade | HTTP simples na 8080 e na 80 vira HTTPS (308); prefixos antigos redirecionam para `/api` |

`scripts/test-gateway.sh` sobe essa configuracao com certificado temporario e servicos simulados e confere cada item acima (inclusive uma rajada de 400 requisicoes que precisa receber 429). Roda no CI a cada push.

### Monitoramento (Prometheus e Grafana)

`compose.monitoring.yml` sobe Prometheus, Grafana e exporters na rede `rede`. So o Grafana e publico, pelo gateway em `/grafana/`, com leitura anonima (papel Viewer); a senha do admin e gerada pelo deploy em `~/.config/matricula/grafana.env` na EC2, fora do Git. O Prometheus fica no loopback da EC2 (`ssh -L 9090:127.0.0.1:9090 ubuntu@13.220.42.157`).

| Alvo (`job`) | Origem das metricas |
|---|---|
| disciplinas, solicitacoes, processamento | `GET /metrics` de cada servico (prometheus-client): HTTP, mensageria e negocio |
| gateway | nginx-prometheus-exporter lendo `stub_status` |
| rabbitmq | plugin `rabbitmq_prometheus` (`/metrics/per-object`, profundidade por fila) |
| postgres | postgres-exporter (conexoes e transacoes por banco) |
| node | node-exporter (CPU, memoria e disco da EC2) |

Painel provisionado: `monitoring/grafana/dashboards/matricula.json` (37 paineis em 8 secoes: visao geral, API, gateway, RabbitMQ, negocio, comunicacao sincrona, infraestrutura e alvos/alertas). Regras de alerta em `monitoring/prometheus/alertas.yml`: alvo fora do ar, 5xx acima de 5%, p95 acima de 1 s, mensagens na DLQ, fila acumulando, worker desconectado, disco e memoria baixos. O CI valida a configuracao e as regras com `promtool` e confere o JSON do painel.

### Teste de carga (Locust)

`loadtest/locustfile.py` exercita o sistema pelo gateway publico com tres perfis simultaneos: alunos navegando (leituras autenticadas), periodo de matricula (solicitacoes que passam por outbox, RabbitMQ, avaliacao, chamadas HTTP entre servicos e reserva de vaga) e visitantes (portal, `/ready` e API protegida sem token, que deve responder 401). Estagios: 10, 40, 90 e 10 usuarios ao longo de 7 minutos.

Com token de administrador, cria uma turma sintetica de capacidade limitada e solicita vagas em nome de alunos ficticios; ao final confere que o numero de deferidas e igual as vagas ocupadas e nunca passa da capacidade. Com token de aluno, solicita para a propria conta uma turma com pre-requisito (no maximo 40 vezes).

```sh
python3 -m venv .venv-carga && .venv-carga/bin/pip install -r loadtest/requirements.txt
# Access token Auth0 do usuario (nunca versione): salve em um arquivo com permissao 600.
cd loadtest && MATRICULA_TOKEN_FILE=~/token.txt ../.venv-carga/bin/locust -f locustfile.py \
  --host https://13.220.42.157:8080 --headless --html resultados/relatorio.html --csv resultados/carga
```

`CARGA_PERFIL=curto` roda em 1 minuto; sem token, so o perfil de visitante e executado. Os resultados ficam em `loadtest/resultados/` (ignorado pelo Git).

## Confiabilidade

Outbox registra eventos na mesma transacao do negocio. Publicacao confirmada, mensagens persistentes, filas duraveis, inbox por evento e resultados com revisao permitem repeticao segura. Falhas de consumo usam retentativa temporizada; a terceira falha envia a mensagem para `matriculas.dlq`.

A DLQ exige inspecao e republicacao apos a causa ser corrigida. Uma chamada entre servicos nao participa de uma transacao distribuida: o endpoint de reserva e idempotente por solicitacao para permitir repeticao apos falha. O teste de promotora garante que a falha de um candidato posterior nao desfaz uma promocao ja feita.

O criterio configurado ordena a **lista de espera**, sem redistribuir vagas ja deferidas. Cada promocao e uma transacao separada; `lista.reavaliar` agenda a continuacao. Posicoes sao calculadas no momento da consulta.

## Etapa 3: Auth0 e frontend

Clone também `matricula-frontend` como diretório irmão. Preencha `.env` usando `.env.example`, execute `python3 scripts/configure.py` e inicie `docker compose up -d --build --wait`. O portal fica em http://localhost:5173. As portas das APIs estão vinculadas ao loopback; acesso externo deve passar pelo gateway.

O script gera um segredo interno em `runtime/.internal-token`, preservado entre execuções e com modo 600, e o replica nos arquivos privados dos três serviços. Não envie esse arquivo ao GitHub nem ao frontend. A credencial permite somente acesso interno limitado; não concede permissões administrativas.

Configure no Auth0 a SPA com callback/logout/web origin `http://localhost:5173`, a API `https://matricula-api`, RBAC e inclusão de permissions. O README do frontend lista os quatro scopes administrativos. Os dados de Domain/Client ID/Audience são públicos; senhas PostgreSQL/RabbitMQ e token interno ficam nos arquivos privados. Não é necessário Client Secret para a SPA.

O script `smoke_e2e.py` agora exige `MATRICULA_ADMIN_TOKEN` no ambiente com um access token Auth0 de administrador. Nunca coloque tokens em commits, argumentos públicos de linha de comando ou logs. Sem um token real, o smoke não valida login com o tenant; os testes automatizados validam JWTs RSA de teste e as regras de autorização.

Para testes locais com portas alternativas:

```sh
TEST_POSTGRES_PORT=55432 TEST_RABBITMQ_PORT=55672 docker compose -f compose.test.yml up -d --wait
DB_PORT=55432 RABBITMQ_URL=amqp://tests:tests@localhost:55672/etapa2-tests bash scripts/test-all.sh
```

Antes de atualizar uma EC2 existente, configure AUTH0_DOMAIN, AUTH0_AUDIENCE e um único INTERNAL_API_TOKEN nos três arquivos privados de runtime. Faça a implantação coordenada para que o processamento não chame APIs protegidas sem a credencial. Publique o frontend e configure HTTPS e URLs permitidas do Auth0. Nenhuma alteração remota é feita pelo setup local.

## HTTPS direto pelo IP publico

`compose.https.yml` publica um gateway Nginx nas portas 80, 443 e 8080 e substitui o proxy HTTP da porta 8080 (pare `compose.proxy.yml` antes de iniciá-lo). A 8080 é a entrada pública em HTTPS porque o Security Group da EC2 libera 80 e 8080, mas não 443; a 443 fica configurada para quando a porta for liberada. A porta 80 permanece em HTTP somente para o desafio ACME da renovação e redireciona o restante para `https://IP:8080`. Requisições HTTP simples enviadas à 8080 recebem `308` para HTTPS (código 497 do Nginx), preservando método e corpo. O gateway encaminha a raiz para `matricula-frontend:80` e `/api/{servico}` para cada serviço na rede `rede`, removendo `X-Service-Token`. Não publica as portas internas dos serviços.

```sh
docker compose -f compose.proxy.yml down
docker compose -f compose.https.yml up -d --wait
```

A EC2 utiliza Certbot 5.4 ou superior em `/opt/matricula-certbot`, com certificado de IP emitido pela Let's Encrypt no perfil `shortlived`. As chaves ficam exclusivamente em `/etc/letsencrypt`, fora do Git. O certificado tem o nome local `matricula-publico`, independentemente do endereço.

Para provisionar em outra máquina, libere TCP 80/443 no firewall e no Security Group. Primeiro sirva `/.well-known/acme-challenge/` a partir de `/var/www/matricula-acme` na porta 80, sem habilitar o bloco TLS antes de o certificado existir. Emita o certificado com:

```sh
sudo /opt/matricula-certbot/bin/certbot certonly \
  --preferred-profile shortlived --webroot \
  --webroot-path /var/www/matricula-acme \
  --ip-address "$PUBLIC_IP" --cert-name matricula-publico
```

Depois aplique `tls/default.conf`, valide com `docker exec matricula-https nginx -t` e recarregue o Nginx. O compose monta o diretório de certificados somente para leitura. As unidades `tls/matricula-certbot.service` e `tls/matricula-certbot.timer`, instaladas em `/etc/systemd/system/`, verificam a renovação a cada seis horas e recarregam o gateway quando necessário. O timer deve permanecer ativo: certificados de IP duram seis dias.

```sh
sudo systemctl enable --now matricula-certbot.timer
sudo systemctl list-timers matricula-certbot.timer
sudo /opt/matricula-certbot/bin/certbot renew --dry-run
```

Na SPA Auth0, acrescente as origens `https://IP_PUBLICO:8080` e `https://IP_PUBLICO` a **Allowed Callback URLs**, **Allowed Logout URLs** e **Allowed Web Origins**, preservando as entradas locais. A porta faz parte da origem: sem `:8080` cadastrado, o Auth0 recusa o login com "Callback URL mismatch". O navegador acessa o IP diretamente, sem túnel e sem depender de processos no computador do usuário. Se o IP da EC2 mudar, reemita o certificado para o novo endereço e atualize as URLs permitidas no Auth0.
