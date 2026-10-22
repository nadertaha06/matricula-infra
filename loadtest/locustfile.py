"""Teste de carga pelo API Gateway publico.

Tres perfis de usuario disputam o sistema ao mesmo tempo:
- AlunoNavegando: catalogo, turmas, minhas matriculas e sessao (leituras autenticadas);
- PeriodoDeMatricula: solicitacoes que percorrem HTTP -> outbox -> RabbitMQ -> avaliacao
  (com chamadas HTTP entre servicos) -> resultado;
- Visitante: portal, arquivos estaticos, /ready dos servicos e API protegida sem token (401).

O modo depende das permissoes do access token Auth0 (nunca o versione):
- administrador: cria uma turma sintetica de capacidade limitada e solicita vagas em nome de
  alunos ficticios; ao final confere que nao houve mais deferimentos que vagas (overbooking);
- aluno: solicita, para a propria conta, uma turma com pre-requisito que ela nao cumpre
  (no maximo CARGA_MAX_SOLICITACOES vezes); cada pedido novo e avaliado e indeferido.
"""
import json
import os
import random
import re
import time
import uuid
from collections import Counter
from pathlib import Path

import requests
from locust import HttpUser, LoadTestShape, between, events, task

RUN = os.environ.get("CARGA_ID") or time.strftime("%m%d%H%M")
CAPACIDADE = int(os.environ.get("CARGA_CAPACIDADE", "100"))
_max = os.environ.get("CARGA_MAX_SOLICITACOES")
RESUMO = Path(os.environ.get("CARGA_RESUMO", f"resultados/resumo-{RUN}.json"))
_token_file = os.environ.get("MATRICULA_TOKEN_FILE")
TOKEN = os.environ.get("MATRICULA_TOKEN") or (
    Path(_token_file).expanduser().read_text().strip() if _token_file else "")
AUTH = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
estado = {"turma_id": None, "solicitacoes": [], "modo": None, "maximo": 0}

# Estagios: aquecimento, carga nominal, pico e recuperacao (segundos acumulados, usuarios).
ESTAGIOS = {
    "completo": [(60, 10), (240, 40), (360, 90), (420, 10)],
    "curto": [(30, 5), (60, 15)],
}[os.environ.get("CARGA_PERFIL", "completo")]


def verificar(resposta, *esperados):
    if resposta.status_code in esperados:
        resposta.success()
    elif resposta.status_code == 429:
        resposta.failure("429 limitado pelo gateway")
    else:
        resposta.failure(f"status {resposta.status_code}")


@events.test_start.add_listener
def preparar(environment, **_):
    if not TOKEN:
        return
    base = environment.host.rstrip("/")
    sessao = requests.get(f"{base}/api/solicitacoes/session", headers=AUTH, timeout=15)
    sessao.raise_for_status()
    permissoes = set(sessao.json()["permissions"])
    if {"manage:oferta", "manage:matriculas"} <= permissoes:
        estado["modo"] = "administrador"
        estado["maximo"] = int(_max or 300)
        preparar_turma_sintetica(base)
    else:
        estado["modo"] = "aluno"
        estado["maximo"] = int(_max or 40)
        turmas = requests.get(f"{base}/api/disciplinas/turmas", headers=AUTH, timeout=15).json()
        alvo = next((t for t in turmas if t["pre_requisitos"]), None)
        if alvo is None:
            raise RuntimeError("Nenhuma turma com pre-requisito para o modo aluno")
        estado["turma_id"] = alvo["id"]
        print(f"Modo aluno: solicitacoes para a turma {alvo['id']} ({alvo['disciplina_codigo']})")


def preparar_turma_sintetica(base):
    """Cria disciplina e turma sinteticas, identificadas pelo RUN, para a disputa de vagas."""
    codigo = f"CARGA{RUN}"
    r = requests.post(f"{base}/api/disciplinas/disciplinas", headers=AUTH, timeout=15,
                      json={"codigo": codigo, "nome": f"Teste de carga {RUN}", "pre_requisitos": []})
    if r.status_code not in (201, 409):
        raise RuntimeError(f"Nao foi possivel criar a disciplina sintetica: {r.status_code} {r.text[:200]}")
    r = requests.post(f"{base}/api/disciplinas/turmas", headers=AUTH, timeout=15, json={
        "disciplina_codigo": codigo, "periodo": f"carga-{RUN}", "capacidade": CAPACIDADE,
        "horarios": [{"dia_semana": 5, "inicio": "07:00", "fim": "08:00"}]})
    r.raise_for_status()
    estado["turma_id"] = r.json()["id"]
    print(f"Turma sintetica {estado['turma_id']} criada com {CAPACIDADE} vagas")


class AlunoNavegando(HttpUser):
    abstract = not TOKEN
    weight = 6
    wait_time = between(1, 3)

    @task(5)
    def turmas(self):
        with self.client.get("/api/disciplinas/turmas", headers=AUTH, catch_response=True) as r:
            verificar(r, 200)

    @task(2)
    def disciplinas(self):
        with self.client.get("/api/disciplinas/disciplinas", headers=AUTH, catch_response=True) as r:
            verificar(r, 200)

    @task(3)
    def minhas_matriculas(self):
        with self.client.get("/api/solicitacoes/matriculas/me", headers=AUTH, catch_response=True) as r:
            verificar(r, 200)

    @task(1)
    def sessao(self):
        with self.client.get("/api/solicitacoes/session", headers=AUTH, catch_response=True) as r:
            verificar(r, 200)


class PeriodoDeMatricula(HttpUser):
    abstract = not TOKEN
    weight = 2
    wait_time = between(1, 3)

    @task(3)
    def solicitar_vaga(self):
        if not estado["turma_id"] or len(estado["solicitacoes"]) >= estado["maximo"]:
            return self.acompanhar()
        corpo = {"turma_id": estado["turma_id"]}
        if estado["modo"] == "administrador":
            corpo["aluno_id"] = f"carga-{RUN}-{uuid.uuid4().hex[:10]}"
        with self.client.post("/api/solicitacoes/matriculas", headers=AUTH, catch_response=True,
                              json=corpo) as r:
            verificar(r, 202)
            # No modo aluno, enquanto a anterior aguarda avaliacao o mesmo id volta (idempotencia).
            if r.status_code == 202 and r.json()["id"] not in estado["solicitacoes"]:
                estado["solicitacoes"].append(r.json()["id"])

    @task(2)
    def acompanhar(self):
        if not estado["solicitacoes"]:
            return
        solicitacao = random.choice(estado["solicitacoes"])
        with self.client.get(f"/api/solicitacoes/matriculas/{solicitacao}", headers=AUTH,
                             name="/api/solicitacoes/matriculas/{id}", catch_response=True) as r:
            verificar(r, 200)


class Visitante(HttpUser):
    weight = 2
    wait_time = between(1, 3)
    asset = None

    def on_start(self):
        pagina = self.client.get("/", name="/ (portal)")
        encontrado = re.search(r'/assets/index-[^"]+\.js', pagina.text or "")
        self.asset = encontrado.group(0) if encontrado else None

    @task(3)
    def portal(self):
        with self.client.get("/", name="/ (portal)", catch_response=True) as r:
            verificar(r, 200)

    @task(2)
    def arquivo_estatico(self):
        if self.asset:
            with self.client.get(self.asset, name="/assets/index-*.js", catch_response=True) as r:
                verificar(r, 200)

    @task(2)
    def prontidao(self):
        servico = random.choice(["disciplinas", "solicitacoes", "processamento"])
        with self.client.get(f"/api/{servico}/ready", name="/api/{servico}/ready", catch_response=True) as r:
            verificar(r, 200)

    @task(2)
    def api_sem_token(self):
        with self.client.get("/api/disciplinas/turmas", name="/api/disciplinas/turmas (sem token)",
                             catch_response=True) as r:
            verificar(r, 401)


class PeriodoDeCarga(LoadTestShape):
    def tick(self):
        agora = self.get_run_time()
        for fim, usuarios in ESTAGIOS:
            if agora < fim:
                return usuarios, 5
        return None


@events.test_stop.add_listener
def conferir_vagas(environment, **_):
    """Espera o processamento assincrono e confere o estado final das solicitacoes do teste."""
    if not TOKEN or not estado["turma_id"]:
        return
    base = environment.host.rstrip("/")
    turma_id = estado["turma_id"]
    criadas = set(estado["solicitacoes"])
    lista_url = (f"{base}/api/solicitacoes/matriculas" if estado["modo"] == "administrador"
                 else f"{base}/api/solicitacoes/matriculas/me")
    limite = time.time() + 180
    while True:
        lista = requests.get(lista_url, headers=AUTH, timeout=30).json()
        nossas = [s for s in lista if s["id"] in criadas]
        pendentes = sum(s["status"] == "SOLICITADA" for s in nossas)
        if not pendentes or time.time() > limite:
            break
        time.sleep(5)
    turma = requests.get(f"{base}/api/disciplinas/turmas/{turma_id}", headers=AUTH, timeout=15).json()
    estados = Counter(s["status"] for s in nossas)
    ocupadas = turma["capacidade"] - turma["vagas_disponiveis"]
    resumo = {
        "carga_id": RUN, "modo": estado["modo"], "turma_id": turma_id, "capacidade": turma["capacidade"],
        "solicitacoes_aceitas": len(estado["solicitacoes"]), "estados": dict(estados),
        "vagas_ocupadas": ocupadas, "pendentes_ao_final": pendentes,
        "sem_overbooking": (estados.get("DEFERIDA", 0) <= turma["capacidade"]
                            if estado["modo"] == "aluno" else
                            estados.get("DEFERIDA", 0) == ocupadas <= turma["capacidade"]),
        "requisicoes": environment.stats.total.num_requests,
        "falhas": environment.stats.total.num_failures,
        "rps_medio": round(environment.stats.total.total_rps, 2),
        "p50_ms": environment.stats.total.get_response_time_percentile(0.50),
        "p95_ms": environment.stats.total.get_response_time_percentile(0.95),
        "p99_ms": environment.stats.total.get_response_time_percentile(0.99),
        "falhas_por_tipo": {f"{e.method} {e.name}: {e.error}": e.occurrences
                            for e in environment.stats.errors.values()},
    }
    RESUMO.parent.mkdir(parents=True, exist_ok=True)
    RESUMO.write_text(json.dumps(resumo, ensure_ascii=False, indent=2))
    print(json.dumps(resumo, ensure_ascii=False, indent=2))
