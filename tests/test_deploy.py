"""Exercise the gateway deployment and its rollback without touching a real Docker host."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'deploy.sh'
FAKE = '''#!/usr/bin/env python3
import json, os, pathlib, sys
root = pathlib.Path(os.environ['DEPLOY_TEST_ROOT'])
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with (root/'calls').open('a') as f: f.write(json.dumps([name,*args])+'\\n')
if name == 'docker':
    if args[0] == 'inspect':
        if '--format' in args:
            if args[-1] == os.environ.get('MISSING'): sys.exit(1)
            print('exited' if args[-1] == os.environ.get('STOPPED') else 'running')
        elif not os.environ.get('LEGACY_PROXY'): sys.exit(1)
    if args[0] == 'run' and os.environ.get('FAIL_NGINX'): sys.exit(1)
    if args[0] == 'ps' and not os.environ.get('NO_8080'): print('0.0.0.0:8080->8080/tcp')
if name == 'git' and args[:2] == ['rev-parse', 'HEAD']: print('commit-anterior')
if name == 'curl':
    if '%{http_code}' in args:
        print(os.environ.get('ACME_CODE', '404'), end='')
    elif os.environ.get('FAIL_HEALTH'):
        sys.exit(22)
    else:
        print('<html><div id="root"></div></html>')
'''
APPS = ['postgres', 'rabbitmq', 'matricula-disciplinas', 'matricula-solicitacoes',
        'matricula-processamento', 'matricula-frontend']


class DeployTests(unittest.TestCase):
    def run_deploy(self, **flags):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('docker', 'git', 'curl', 'sudo', 'flock', 'sleep'):
                path = root / name
                path.write_text(FAKE)
                path.chmod(0o755)
            repo = root / 'repo'
            (repo / 'tls').mkdir(parents=True)
            (repo / 'proxy').mkdir()
            for file in ('compose.https.yml', 'compose.proxy.yml', 'tls/default.conf', 'proxy/default.conf'):
                (repo / file).write_text('')
            env = dict(os.environ, HOME=directory, DEPLOY_TEST_ROOT=directory, INFRA_DIR=str(repo),
                       PATH=directory+os.pathsep+os.environ['PATH'], **flags)
            result = subprocess.run(['bash', str(SCRIPT), 'commit-novo'], env=env,
                                    capture_output=True, text=True, timeout=30)
            calls_file = root / 'calls'
            calls = [json.loads(line) for line in calls_file.read_text().splitlines()] if calls_file.exists() else []
            return result, calls

    @staticmethod
    def compose_up(calls, file):
        return ['docker', 'compose', '-f', file, 'up', '-d', '--wait'] in calls

    def test_deploy_replaces_legacy_proxy_and_checks_every_service(self):
        result, calls = self.run_deploy(LEGACY_PROXY='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(['git', 'reset', '-q', '--hard', 'commit-novo'], calls)
        self.assertIn(['docker', 'rm', '-f', 'matricula-proxy'], calls)
        self.assertTrue(self.compose_up(calls, 'compose.https.yml'))
        self.assertIn(['docker', 'exec', 'matricula-https', 'nginx', '-s', 'reload'], calls)
        self.assertIn(['sudo', 'systemctl', 'enable', '--now', 'matricula-certbot.timer'], calls)
        urls = {c[-1] for c in calls if c[0] == 'curl'}
        for service in ('disciplinas', 'solicitacoes', 'processamento'):
            self.assertIn(f'https://127.0.0.1:8080/api/{service}/ready', urls)
        self.assertNotIn(['git', 'reset', '-q', '--hard', 'commit-anterior'], calls)

    def test_nginx_is_validated_before_replacing_the_proxy(self):
        _, calls = self.run_deploy(LEGACY_PROXY='1')
        nginx_test = next(i for i, c in enumerate(calls) if c[:2] == ['docker', 'run'])
        proxy_removed = calls.index(['docker', 'rm', '-f', 'matricula-proxy'])
        self.assertLess(nginx_test, proxy_removed)

    def test_stopped_application_container_is_started(self):
        result, calls = self.run_deploy(STOPPED='matricula-processamento')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(['docker', 'start', 'matricula-processamento'], calls)
        for app in APPS:
            self.assertIn(['docker', 'inspect', '--format', '{{.State.Status}}', app], calls)

    def test_missing_application_container_aborts_before_any_change(self):
        result, calls = self.run_deploy(MISSING='matricula-frontend')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('pipeline do servico', result.stderr)
        self.assertFalse(any(c[0] in ('git', 'sudo') for c in calls))
        self.assertFalse(any(c[:2] == ['docker', 'compose'] for c in calls))

    def test_invalid_nginx_config_restores_previous_commit_and_keeps_proxy(self):
        result, calls = self.run_deploy(LEGACY_PROXY='1', FAIL_NGINX='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(['git', 'reset', '-q', '--hard', 'commit-anterior'], calls)
        self.assertNotIn(['docker', 'rm', '-f', 'matricula-proxy'], calls)
        self.assertFalse(any(c[0] == 'sudo' for c in calls))

    def test_unhealthy_gateway_rolls_back(self):
        result, calls = self.run_deploy(FAIL_HEALTH='1')
        self.assertNotEqual(result.returncode, 0)
        reset_new = calls.index(['git', 'reset', '-q', '--hard', 'commit-novo'])
        reset_old = calls.index(['git', 'reset', '-q', '--hard', 'commit-anterior'])
        self.assertLess(reset_new, reset_old)
        self.assertIn('Deploy falhou', result.stderr)

    def test_rollback_restores_http_proxy_when_port_8080_is_free(self):
        result, calls = self.run_deploy(FAIL_HEALTH='1', NO_8080='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.compose_up(calls, 'compose.proxy.yml'))

    def test_acme_challenge_must_stay_on_plain_http(self):
        result, calls = self.run_deploy(ACME_CODE='308')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(['git', 'reset', '-q', '--hard', 'commit-anterior'], calls)


if __name__ == '__main__':
    unittest.main()
