"""Generate private local runtime files from this deployment's .env."""
import os
from pathlib import Path
from urllib.parse import quote

root = Path(__file__).resolve().parents[1]
values = {}
for line in (root / '.env').read_text().splitlines():
    if line.strip() and not line.lstrip().startswith('#'):
        key, value = line.split('=', 1)
        values[key] = value.strip().strip('"').strip("'")
required = ['POSTGRES_PASSWORD', 'RABBITMQ_USER', 'RABBITMQ_PASSWORD']
if any(not values.get(k) for k in required):
    raise SystemExit('Preencha POSTGRES_PASSWORD, RABBITMQ_USER e RABBITMQ_PASSWORD no .env')
directory = root / 'runtime'
directory.mkdir(mode=0o700, exist_ok=True)
for service, port in [('disciplinas', 8001), ('solicitacoes', 8002), ('processamento', 8003)]:
    env = {
        'APP_NAME': 'matricula-' + service, 'ROOT_PATH': '/api/' + service, 'PORT': str(port), 'DB_HOST': 'postgres', 'DB_PORT': '5432',
        'DB_NAME': service + '_db', 'DB_USER': values.get('POSTGRES_USER', 'postgres'),
        'DB_PASSWORD': values['POSTGRES_PASSWORD'],
        'RABBITMQ_URL': 'amqp://' + quote(values['RABBITMQ_USER'], safe='') + ':' + quote(values['RABBITMQ_PASSWORD'], safe='') + '@rabbitmq:5672/',
        'WORKER_ENABLED': 'true', 'DISCIPLINAS_URL': 'http://matricula-disciplinas:8001',
        'SOLICITACOES_URL': 'http://matricula-solicitacoes:8002', 'CRITERIO_DESEMPATE': 'coeficiente',
    }
    path = directory / (service + '.env')
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(''.join(k + '=' + v + '\n' for k, v in env.items()))
print('Arquivos privados gerados em runtime/.')
