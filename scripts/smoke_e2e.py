#!/usr/bin/env python3
"""Exercise the real three-service/RabbitMQ flow using only synthetic data."""
import argparse
import json
import time
import uuid
import urllib.request
import urllib.error


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--timeout', type=float, default=45)
    args = parser.parse_args()
    prefix = uuid.uuid4().hex[:8]
    urls = {s: f'http://{args.host}:{p}' for s,p in [('disciplinas',8001),('solicitacoes',8002),('processamento',8003)]}

    def request(service, method, path, data=None, expected=200):
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(urls[service]+path, data=body, method=method, headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        if status != expected:
            raise AssertionError(f'{service} {method} {path}: {status}, esperado {expected}; {raw.decode()}')
        return json.loads(raw) if raw else None

    def wait(sid, expected, motivo=None):
        deadline = time.monotonic() + args.timeout
        result = None
        while time.monotonic() < deadline:
            result = request('solicitacoes','GET','/matriculas/'+sid)
            if result['status'] == expected:
                if motivo is not None:
                    assert result['motivo'] == motivo, result
                return result
            time.sleep(.3)
        raise AssertionError(f'Solicitacao {sid}: esperava {expected}, recebido {result}')

    for service in urls:
        assert request(service,'GET','/ready')['status'] == 'ready'
    pre, course, conflict = ('PRE'+prefix,'CUR'+prefix,'CON'+prefix)
    for codigo, prereqs in [(pre,[]),(course,[pre]),(conflict,[])]:
        request('disciplinas','POST','/disciplinas',{'codigo':codigo,'nome':'Demonstracao '+codigo,'pre_requisitos':prereqs},201)
    def offer(code, capacity):
        return request('disciplinas','POST','/turmas',{'disciplina_codigo':code,'periodo':'2026-2','capacidade':capacity,
            'horarios':[{'dia_semana':1,'inicio':'08:00','fim':'10:00'}]},201)['id']
    tid, clash_tid = offer(course,1), offer(conflict,5)
    students = {n: f'demo-{prefix}-{n}' for n in ['a','b','c','d']}
    for name, coeff in [('a',7),('b',9),('c',8),('d',10)]:
        request('solicitacoes','PUT',f'/alunos/{students[name]}/historico',
            {'disciplinas':[] if name == 'd' else [pre],'coeficiente':coeff,'periodo_aluno':3})
    request('processamento','PUT',f'/turmas/{tid}/criterio',{'criterio':'coeficiente'})
    def enroll(name, turma_id=tid):
        return request('solicitacoes','POST','/matriculas',{'aluno_id':students[name],'turma_id':turma_id},202)['id']
    a = enroll('a')
    wait(a,'DEFERIDA')
    assert enroll('a') == a, 'Solicitacao duplicada gerou outra matricula'
    b = enroll('b')
    wait(b,'EM_ESPERA')
    c = enroll('c')
    wait(c,'EM_ESPERA')
    d = enroll('d')
    wait(d,'INDEFERIDA','PRE_REQUISITO')
    clash = enroll('a',clash_tid)
    wait(clash,'INDEFERIDA','CHOQUE_HORARIO')
    queue = request('processamento','GET',f'/turmas/{tid}/lista-espera')
    assert [x['solicitacao_id'] for x in queue] == [b,c], queue
    assert request('disciplinas','GET',f'/turmas/{tid}')['vagas_disponiveis'] == 0
    request('solicitacoes','DELETE','/matriculas/'+a)
    wait(a,'CANCELADA')
    wait(b,'DEFERIDA')
    wait(c,'EM_ESPERA')
    request('solicitacoes','DELETE','/matriculas/'+b)
    wait(c,'DEFERIDA')
    deadline = time.monotonic()+args.timeout
    while request('processamento','GET',f'/turmas/{tid}/lista-espera') and time.monotonic() < deadline:
        time.sleep(.2)
    assert request('processamento','GET',f'/turmas/{tid}/lista-espera') == []
    assert request('disciplinas','GET',f'/turmas/{tid}')['vagas_disponiveis'] == 0
    history = request('solicitacoes','GET',f'/matriculas/{b}/historico')
    assert [x['status'] for x in history] == ['SOLICITADA','EM_ESPERA','DEFERIDA','CANCELADA'], history
    print(json.dumps({'resultado':'PASS','turma_id':tid,'matriculas':{'cancelada':a,'promovida_e_cancelada':b,'promovida':c,'sem_pre_requisito':d,'choque_horario':clash},
        'cenarios':['readiness dos tres servicos','persistencia','HTTP entre servicos','RabbitMQ assincrono','idempotencia','pre-requisitos','choque de horario','capacidade','desempate','cancelamento','promocao automatica','historico de estados']},indent=2,ensure_ascii=False))


if __name__ == '__main__':
    main()
