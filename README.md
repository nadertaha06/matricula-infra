# matricula-infra

Composicao de infraestrutura compartilhada pelos servicos de matricula.
Este repositorio **nao tem servico proprio**: ele so sobe o Postgres e o
RabbitMQ que os tres microsservicos consomem.

| Componente | Imagem | Container | Portas publicadas |
| --- | --- | --- | --- |
| Postgres | `postgres:16` | `postgres` | nenhuma (so pela rede `rede`) |
| RabbitMQ | `rabbitmq:3-management` | `rabbitmq` | `5672` (AMQP), `15672` (painel) |

## Servicos que dependem desta infraestrutura

| Servico | Porta | Banco |
| --- | --- | --- |
| `matricula-disciplinas` | 8001 | `disciplinas_db` |
| `matricula-solicitacoes` | 8002 | `solicitacoes_db` |
| `matricula-processamento` | 8003 | `processamento_db` |

## O nome do host do banco

O container do Postgres se chama **`postgres`** (`container_name: postgres` no
`docker-compose.yml`). Como todos os containers estao na mesma rede bridge
`rede`, esse nome e o hostname resolvido pelo DNS interno do Docker.

Por isso, **`DB_HOST=postgres` nos tres workflows de deploy tem que bater
exatamente com esse `container_name`**. Se um dia o `container_name` mudar aqui,
os tres `.github/workflows/deploy.yml` precisam mudar junto, ou os servicos
sobem e nao conseguem resolver o banco.

## Pre-requisitos ja atendidos na EC2

- Instancia EC2 acessivel por SSH com o usuario `ubuntu`.
- Docker instalado.
- Rede bridge `rede` ja criada (por isso o compose a declara com
  `external: true`).

Conferindo que a rede existe:

```bash
docker network ls | grep rede
```

Se por algum motivo ela nao existir:

```bash
docker network create rede
```

## Subindo o compose

Na EC2, como usuario `ubuntu`:

```bash
git clone https://github.com/nadertaha06/matricula-infra.git
cd matricula-infra

cp .env.example .env
nano .env          # defina POSTGRES_PASSWORD e RABBITMQ_PASSWORD reais

docker compose up -d
```

Conferindo:

```bash
docker compose ps
docker compose logs -f postgres

# os tres bancos devem aparecer
docker exec -it postgres psql -U postgres -c "\l"
```

Painel do RabbitMQ: `http://<IP-DA-EC2>:15672` (usuario e senha do `.env`).

### Parando

```bash
docker compose down       # mantem os dados no volume postgres_data
docker compose down -v    # APAGA os dados e permite reexecutar o init
```

> O `init-databases.sh` so roda na primeira subida, quando o volume
> `postgres_data` esta vazio. Depois disso ele e ignorado.

## Portas a liberar no Security Group

| Porta | Protocolo | Para que |
| --- | --- | --- |
| 22 | TCP | SSH — usado pelo deploy do GitHub Actions |
| 5672 | TCP | RabbitMQ (AMQP) |
| 8001 | TCP | `matricula-disciplinas` |
| 8002 | TCP | `matricula-solicitacoes` |
| 8003 | TCP | `matricula-processamento` |
| 15672 | TCP | Painel de administracao do RabbitMQ |

A porta 5432 do Postgres **nao** e publicada: os servicos falam com o banco pela
rede `rede`, sem passar pelo host.

## Secrets a cadastrar no GitHub

Cadastre em **cada um dos tres repositorios de servico**
(`matricula-disciplinas`, `matricula-solicitacoes`, `matricula-processamento`),
em *Settings > Secrets and variables > Actions > New repository secret*:

| Secret | Conteudo |
| --- | --- |
| `DOCKERHUB_TOKEN` | Access token do DockerHub do usuario `nadertaha06` (Account Settings > Personal access tokens, permissao Read & Write) |
| `HOST_TEST` | IP publico ou DNS da EC2 |
| `KEY_TEST` | Conteudo da chave SSH **privada** do usuario `ubuntu`, incluindo as linhas `-----BEGIN ...-----` e `-----END ...-----` |
| `DB_PASSWORD` | Mesma senha definida em `POSTGRES_PASSWORD` no `.env` desta EC2 |
| `RABBITMQ_URL` | `amqp://<RABBITMQ_USER>:<RABBITMQ_PASSWORD>@rabbitmq:5672/` |
| `AUTH0_DOMAIN` | Dominio do tenant Auth0, ex.: `seu-tenant.us.auth0.com` |
| `AUTH0_AUDIENCE` | Audience da API cadastrada no Auth0 |

Este repositorio (`matricula-infra`) **nao precisa de secret nenhum**: ele nao
tem workflow de deploy.

## Regra de ouro

Nenhuma senha, token ou chave real entra em arquivo versionado. O `.env` esta no
`.gitignore`; o `.env.example` so tem valores de exemplo.
