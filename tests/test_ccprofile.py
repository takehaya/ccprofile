import contextlib
import io
import json
import os
from pathlib import Path
import pty
import runpy
import select
import signal
import stat
import subprocess
import sys
import tempfile
import termios
import time
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
# Exercise the installed, concatenated executable in every subprocess test.
_installed = tempfile.TemporaryDirectory(prefix='ccprofile-tests-')
CLI = Path(_installed.name) / 'ccprofile'
subprocess.run(['sh', str(ROOT / 'install.sh'), '--local'], check=True,
               env={**os.environ, 'CCPROFILE_INSTALL_DIR': _installed.name},
               capture_output=True)
cc = types.SimpleNamespace(**runpy.run_path(str(ROOT / 'src' / 'ccprofile.py')))
FAKE_TOKEN = 'NONSECRET_TEST_TOKEN'
FAKE = r'''#!/usr/bin/env python3
import json, os, signal, sys
from pathlib import Path
if sys.argv[1:] == ['auth', 'login', '--help']:
    print('Usage: claude' if os.environ.get('FAKE_UNSUPPORTED') else 'Usage: claude auth login [options]')
    sys.exit(0)
mode = os.environ.get('FAKE_MODE')
if mode == 'signals':
    signal.signal(signal.SIGINT, lambda *_: sys.exit(70))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(71))
record = {'args':sys.argv[1:], 'env':dict(os.environ)}
Path(os.environ['FAKE_OUTPUT']).write_text(json.dumps(record))
if mode == 'signals':
    while True: signal.pause()
sys.exit(int(os.environ.get('FAKE_EXIT', '0')))
'''


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.fakebin = self.home / 'fakebin'
        self.fakebin.mkdir()
        (self.fakebin / 'claude').write_text(FAKE)
        (self.fakebin / 'claude').chmod(0o755)
        self.output = self.home / 'record.json'
        # Construct an environment without real credentials or host config.
        self.env = {'HOME':str(self.home), 'PATH':str(self.fakebin) + os.pathsep + os.environ['PATH'],
                    'FAKE_OUTPUT':str(self.output), 'TERM':'xterm', 'LANG':'C.UTF-8'}
        self.store = cc.Store(self.home)

    def invoke(self, *args, extra=None, expected=0):
        env = dict(self.env)
        env.update(extra or {})
        p = subprocess.run([str(CLI), *args], env=env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=10, cwd=self.home)
        self.assertEqual(p.returncode, expected, p.stdout + p.stderr)
        self.assertNotIn(FAKE_TOKEN, p.stdout + p.stderr)
        return p

    def add_api(self, name='gateway', auth='bearer'):
        self.invoke('add', name, '--type', 'api', '--base-url', 'https://example.com/api',
                    '--model', 'model-v1', '--auth', auth)

    def record(self):
        return json.loads(self.output.read_text())

    def test_crud_and_arbitrary_names(self):
        self.invoke('list')
        self.assertFalse(self.store.path.exists())
        self.invoke('add', 'Private_7', '--type', 'claude')
        self.add_api('AnyService')
        listing = self.invoke('list').stdout
        listed_rows = [line.split() for line in listing.splitlines()[1:]]
        self.assertIn(['Private_7', 'claude', '-', '-', '-'], listed_rows)
        self.assertEqual(listed_rows[0][:3], ['AnyService', 'api', 'model-v1'])
        self.assertEqual(json.loads(self.invoke('show', 'AnyService').stdout)['auth'], 'bearer')
        self.invoke('edit', 'AnyService', '--base-url', 'https://other.example/v1', '--model', 'new-model', '--auth', 'x-api-key')
        updated = json.loads(self.invoke('show', 'AnyService').stdout)
        self.assertEqual(updated['model'], 'new-model')
        self.assertEqual(updated['auth'], 'x-api-key')
        self.invoke('remove', 'AnyService')
        self.invoke('show', 'AnyService', expected=1)
        self.assertIn('Private_7', self.store.load()['profiles'])

    def test_validation_and_safe_errors(self):
        self.invoke('add', 'okay', '--type', 'claude')
        old = self.store.path.read_bytes()
        cases = [
            ('add', 'okay', '--type', 'claude'),
            ('add', '../escape', '--type', 'claude'),
            ('add', '1invalid', '--type', 'claude'),
            ('add', 'x'*65, '--type', 'claude'),
            ('add', 'bad', '--type', 'claude', '--model', ''),
            ('add', 'bad', '--type', 'api'),
            ('add', 'bad', '--type', 'api', '--use-default-config'),
        ]
        for args in cases:
            with self.subTest(args=args):
                self.invoke(*args, expected=1)
                self.assertEqual(self.store.path.read_bytes(), old)
        for url in ('http://localhost', 'https://user:' + FAKE_TOKEN + '@example.com',
                    'https://example.com?token=' + FAKE_TOKEN, 'https://example.com#',
                    'https://example.com?', 'https://example.com/v1/messages',
                    'https://example.com/%6dessages', 'https://', 'https://example.com:99999',
                    'https://example.com:abc', 'https://example.com/path\n'):
            with self.subTest(url=url):
                self.invoke('add', 'bad', '--type', 'api', '--base-url', url,
                            '--model', 'm', '--auth', 'bearer', expected=1)
        self.invoke('add', 'bad', '--type', FAKE_TOKEN, expected=2)
        self.invoke('add', 'bad', '--type', 'api', '--token', FAKE_TOKEN, expected=2)

    def test_default_config_only_when_explicit(self):
        self.invoke('add', 'missing', '--type', 'claude', '--use-default-config', expected=1)
        default = self.home / '.claude'
        default.mkdir()
        original = default / 'original-credential'
        original.write_text('existing placeholder')
        self.invoke('add', 'default', '--type', 'claude', '--use-default-config')
        self.invoke('add', 'second', '--type', 'claude', '--use-default-config', expected=1)
        self.invoke('add', 'isolated', '--type', 'claude')
        self.invoke('run', 'isolated')
        self.assertNotEqual(self.record()['env']['CLAUDE_CONFIG_DIR'], str(default))
        self.invoke('run', 'default')
        self.assertNotIn('CLAUDE_CONFIG_DIR', self.record()['env'])
        self.assertNotIn('ANTHROPIC_CONFIG_DIR', self.record()['env'])
        self.invoke('remove', 'default')
        self.assertEqual(original.read_text(), 'existing placeholder')

    def test_environment_auth_args_and_parent_unchanged(self):
        self.add_api()
        self.invoke('add', 'subscription', '--type', 'claude')
        conflicting = {
            'ANTHROPIC_BASE_URL':'https://wrong.example', 'ANTHROPIC_API_KEY':'OLD_KEY',
            'ANTHROPIC_AUTH_TOKEN':'OLD_AUTH', 'ANTHROPIC_CUSTOM_HEADERS':'Authorization: old',
            'ANTHROPIC_MODEL':'wrong-model', 'ANTHROPIC_PROFILE':'other',
            'ANTHROPIC_CONFIG_DIR':'/wrong-anthropic-scope',
            'CLAUDE_CONFIG_DIR':'/wrong', 'CLAUDE_CODE_USE_BEDROCK':'1',
            'CLAUDE_CODE_USE_VERTEX':'1', 'CLAUDE_CODE_USE_FOUNDRY':'1',
            'CLAUDE_CODE_USE_MANTLE':'1', 'CLAUDE_CODE_USE_ANTHROPIC_AWS':'1',
            'CLAUDE_CODE_OAUTH_TOKEN':'OLD_OAUTH', 'CLAUDE_CODE_OAUTH_REFRESH_TOKEN':'OLD_REFRESH',
            'AWS_BEARER_TOKEN_BEDROCK':'OLD_AWS', 'GOOGLE_APPLICATION_CREDENTIALS':'/old',
            'ANTHROPIC_FUTURE_ROUTING':'unknown', 'CLAUDE_CODE_FUTURE_PROVIDER':'1',
            'HTTPS_PROXY':'https://proxy.example', 'NO_PROXY':'localhost',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC':'1', 'CCPROFILE_TOKEN':FAKE_TOKEN,
        }
        original = dict(conflicting)
        special = ['-p', 'spaces and $HOME; $(echo nope) "quoted"', '--dangerously-skip-permissions', 'line\nbreak']
        self.invoke('run', 'gateway', '--', *special, extra=conflicting)
        record = self.record()
        self.assertEqual(record['args'], ['--model', 'model-v1', *special])
        child = record['env']
        self.assertEqual(child['ANTHROPIC_AUTH_TOKEN'], FAKE_TOKEN)
        self.assertEqual(child['ANTHROPIC_BASE_URL'], 'https://example.com/api')
        self.assertNotIn('ANTHROPIC_API_KEY', child)
        for key in conflicting:
            if cc.managed_env(key) and key not in ('ANTHROPIC_BASE_URL', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CONFIG_DIR', 'ANTHROPIC_CONFIG_DIR'):
                self.assertNotIn(key, child)
        self.assertEqual(child['HTTPS_PROXY'], conflicting['HTTPS_PROXY'])
        self.assertEqual(child['CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC'], '1')
        api_dir = child['CLAUDE_CONFIG_DIR']
        self.assertEqual(child['ANTHROPIC_CONFIG_DIR'], str(Path(api_dir) / 'anthropic'))
        self.invoke('run', 'subscription', extra=conflicting)
        child = self.record()['env']
        self.assertNotEqual(child['CLAUDE_CONFIG_DIR'], api_dir)
        self.assertEqual(child['ANTHROPIC_CONFIG_DIR'], str(Path(child['CLAUDE_CONFIG_DIR']) / 'anthropic'))
        for key in ('ANTHROPIC_BASE_URL', 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CCPROFILE_TOKEN'):
            self.assertNotIn(key, child)
        self.assertEqual(conflicting, original)
        self.invoke('edit', 'gateway', '--auth', 'x-api-key')
        self.invoke('run', 'gateway', extra={'CCPROFILE_TOKEN':FAKE_TOKEN})
        child = self.record()['env']
        self.assertEqual(child['ANTHROPIC_API_KEY'], FAKE_TOKEN)
        self.assertNotIn('ANTHROPIC_AUTH_TOKEN', child)

    def test_models_missing_token_and_missing_claude(self):
        self.add_api()
        for args in (['--model', 'other'], ['--model=other']):
            self.invoke('run', 'gateway', '--', *args, extra={'CCPROFILE_TOKEN':FAKE_TOKEN}, expected=1)
        self.invoke('run', 'gateway', '-p', 'hello', expected=2)
        self.invoke('run', 'gateway', extra={'CCPROFILE_TOKEN':''}, expected=1)
        self.assertFalse(self.output.exists())
        with mock.patch.object(cc.os, 'open', side_effect=OSError):
            with self.assertRaises(cc.SafeError):
                cc.read_token({})
        # Main cannot launch if token input fails.
        with mock.patch.dict(cc.main.__globals__, {'read_token':mock.Mock(side_effect=cc.SafeError('API token is required')),
                                                   'check_settings':mock.Mock()}), mock.patch.object(cc.os, 'execve') as execute:
            with mock.patch.dict(os.environ, self.env, clear=True):
                with self.assertRaises(cc.SafeError):
                    cc.main(['run', 'gateway'], self.store)
            execute.assert_not_called()
        self.invoke('run', 'gateway', extra={'PATH':os.path.dirname(sys.executable)}, expected=1)

    def test_permissions_paths_and_failed_update(self):
        self.invoke('add', 'account', '--type', 'claude')
        self.assertEqual(stat.S_IMODE(self.store.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.store.root.stat().st_mode), 0o700)
        original = self.store.path.read_bytes()
        with mock.patch.object(cc.os, 'replace', side_effect=OSError('test disk failure')):
            with self.assertRaises(OSError):
                self.store.update(lambda c: c['profiles'].clear())
        self.assertEqual(self.store.path.read_bytes(), original)
        self.assertEqual(list(self.store.root.glob('.config-*')), [])
        self.store.path.chmod(0o644)
        self.invoke('list', expected=1)
        self.store.path.chmod(0o600)
        self.store.root.chmod(0o755)
        self.invoke('list', expected=1)
        self.store.root.chmod(0o700)
        self.store.path.unlink()
        self.store.path.symlink_to(self.home / 'unmanaged')
        self.invoke('list', expected=1)
        self.store.path.unlink()
        self.store.path.write_text('version = 1\n[profiles.bad]\nid = "../../escape"\ntype = "claude"\n')
        self.store.path.chmod(0o600)
        self.invoke('run', 'bad', expected=1)

    def test_remove_retains_and_recreate_never_reuses(self):
        self.invoke('add', 'account', '--type', 'claude')
        self.invoke('run', 'account')
        directory = Path(self.record()['env']['CLAUDE_CONFIG_DIR'])
        sentinel = directory / 'history'
        sentinel.write_text('retained')
        old_id = self.store.load()['profiles']['account']['id']
        self.invoke('remove', 'account')
        self.assertEqual(sentinel.read_text(), 'retained')
        self.invoke('add', 'account', '--type', 'claude')
        self.assertNotEqual(self.store.load()['profiles']['account']['id'], old_id)
        self.invoke('run', 'account')
        self.assertNotEqual(Path(self.record()['env']['CLAUDE_CONFIG_DIR']), directory)
        self.invoke('remove', 'account', '--purge', expected=2)

    def test_secret_free_output_and_settings_detection(self):
        self.add_api()
        env = {'CCPROFILE_TOKEN':FAKE_TOKEN}
        self.invoke('list', extra=env)
        self.invoke('show', 'gateway', extra=env)
        # doctor reports unsupported OS on Linux but runs the other checks.
        self.invoke('doctor', 'gateway', extra=env, expected=0 if sys.platform == 'darwin' else 1)
        p = self.store.load()['profiles']['gateway']
        directory = self.store.ensure_dir(p)
        settings = directory / 'settings.json'
        settings.write_text(json.dumps({'env':{'ANTHROPIC_AUTH_TOKEN':FAKE_TOKEN}}))
        report = self.invoke('doctor', 'gateway', extra=env, expected=1)
        self.assertIn('settings conflict', report.stdout)
        self.invoke('run', 'gateway', extra=env, expected=1)
        self.assertFalse(self.output.exists())
        settings.write_text('{"secret":"' + FAKE_TOKEN)
        self.invoke('doctor', 'gateway', extra=env, expected=1)
        self.invoke('run', 'gateway', extra=env, expected=1)
        settings.unlink()
        project = self.home / '.claude'
        project.mkdir()
        (project / 'settings.local.json').write_text(json.dumps({'apiKeyHelper':FAKE_TOKEN}))
        self.invoke('run', 'gateway', extra=env, expected=1)
        self.store.path.write_text('version = "' + FAKE_TOKEN + '"\ninvalid')
        self.invoke('list', extra=env, expected=1)
        self.invoke('doctor', extra=env, expected=1)

    def test_login_probe_and_isolation(self):
        self.invoke('add', 'account', '--type', 'claude')
        self.invoke('login', 'account', extra={'ANTHROPIC_API_KEY':'old', 'CLAUDE_CODE_OAUTH_REFRESH_TOKEN':'old'})
        record = self.record()
        self.assertEqual(record['args'], ['auth', 'login'])
        self.assertNotIn('ANTHROPIC_API_KEY', record['env'])
        self.assertNotIn('CLAUDE_CODE_OAUTH_REFRESH_TOKEN', record['env'])
        self.invoke('login', 'account', extra={'FAKE_UNSUPPORTED':'1'}, expected=1)
        self.add_api()
        self.invoke('login', 'gateway', expected=1)

    def test_exit_codes_and_signals(self):
        self.invoke('add', 'account', '--type', 'claude')
        self.invoke('run', 'account', extra={'FAKE_EXIT':'37'}, expected=37)
        for sig, code in ((signal.SIGINT, 70), (signal.SIGTERM, 71)):
            self.output.unlink(missing_ok=True)
            env = dict(self.env, FAKE_MODE='signals')
            with subprocess.Popen([str(CLI), 'run', 'account'], env=env,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, cwd=self.home) as p:
                try:
                    deadline = time.monotonic() + 5
                    while not self.output.exists() and time.monotonic() < deadline:
                        if p.poll() is not None:
                            self.fail('child exited before signal test')
                        time.sleep(0.01)
                    self.assertTrue(self.output.exists())
                    p.send_signal(sig)
                    p.communicate(timeout=5)
                    self.assertEqual(p.returncode, code)
                finally:
                    if p.poll() is None:
                        p.kill()
                        p.communicate()

    def test_terminal_token_is_hidden_and_echo_restored(self):
        self.add_api()
        for terminate in (False, True):
            with self.subTest(terminate=terminate):
                self.output.unlink(missing_ok=True)
                pid, master = pty.fork()
                if pid == 0:
                    os.chdir(self.home)
                    os.execve(str(CLI), [str(CLI), 'run', 'gateway'], self.env)
                captured = b''
                reaped = False
                try:
                    deadline = time.monotonic() + 5
                    while b'API token (not saved): ' not in captured and time.monotonic() < deadline:
                        if select.select([master], [], [], 0.1)[0]:
                            try:
                                captured += os.read(master, 4096)
                            except OSError:
                                break
                    self.assertIn(b'API token (not saved): ', captured)
                    self.assertFalse(termios.tcgetattr(master)[3] & termios.ECHO)
                    if terminate:
                        os.kill(pid, signal.SIGTERM)
                    else:
                        os.write(master, (FAKE_TOKEN + '\n').encode())
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        found, status = os.waitpid(pid, os.WNOHANG)
                        if found:
                            reaped = True
                            break
                        if select.select([master], [], [], 0.1)[0]:
                            with contextlib.suppress(OSError):
                                captured += os.read(master, 4096)
                    self.assertTrue(reaped, 'terminal child did not exit')
                    self.assertEqual(os.waitstatus_to_exitcode(status), 143 if terminate else 0)
                    self.assertNotIn(FAKE_TOKEN.encode(), captured)
                    self.assertTrue(termios.tcgetattr(master)[3] & termios.ECHO)
                    if terminate:
                        self.assertFalse(self.output.exists())
                    else:
                        self.assertEqual(self.record()['env']['ANTHROPIC_AUTH_TOKEN'], FAKE_TOKEN)
                finally:
                    if not reaped:
                        os.kill(pid, signal.SIGKILL)
                        os.waitpid(pid, 0)
                    os.close(master)

    def test_concurrent_updates_and_launches(self):
        processes = [subprocess.Popen([str(CLI), 'add', 'account' + str(i), '--type', 'claude'],
                                      env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      cwd=self.home) for i in range(12)]
        for p in processes:
            out, err = p.communicate(timeout=10)
            self.assertEqual(p.returncode, 0, out + err)
        self.assertEqual(len(self.store.load()['profiles']), 12)
        launched = []
        for i in (0, 1):
            path = self.home / ('launch' + str(i))
            env = dict(self.env, FAKE_OUTPUT=str(path))
            p = subprocess.Popen([str(CLI), 'run', 'account' + str(i)], env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.home)
            launched.append((p, path))
        records = []
        for p, path in launched:
            p.communicate(timeout=10)
            self.assertEqual(p.returncode, 0)
            records.append(json.loads(path.read_text()))
        self.assertNotEqual(records[0]['env']['CLAUDE_CONFIG_DIR'], records[1]['env']['CLAUDE_CONFIG_DIR'])

    def test_help_and_installer(self):
        self.invoke('--version')
        self.invoke('--help')
        for command in ('add', 'list', 'show', 'edit', 'remove', 'login', 'run', 'doctor'):
            self.invoke(command, '--help')
        install = self.home / 'install dir'
        result = subprocess.run(['sh', str(ROOT / 'install.sh'), '--local'],
                                env=dict(self.env, CCPROFILE_INSTALL_DIR=str(install)),
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((install / 'ccprofile').is_file())
        self.assertEqual((install / 'ccprofile').read_bytes(), CLI.read_bytes())
        self.assertFalse((self.home / '.zshrc').exists())
        installed = subprocess.run([str(install / 'ccprofile'), '--version'], capture_output=True, text=True)
        self.assertEqual(installed.returncode, 0)
        self.assertIn('ccprofile 0.1.0', installed.stdout)

    def test_remote_installer_concatenation_and_failed_download(self):
        # Feed the installer through stdin, as in curl | sh; all downloads
        # use fixtures so this also covers a partial second-file failure.
        curl = self.fakebin / 'curl'
        curl.write_text('''#!/usr/bin/env python3
import os, sys
from pathlib import Path
name = sys.argv[-1].rsplit('/', 1)[-1]
if name not in ('launcher.sh', 'ccprofile.py'):
    sys.exit(22)
if name == 'ccprofile.py' and os.environ.get('FAIL_DOWNLOAD'):
    sys.stdout.write('partial download')
    sys.exit(22)
sys.stdout.buffer.write((Path(os.environ['SOURCE_DIR']) / name).read_bytes())
''')
        curl.chmod(0o755)
        install = self.home / 'remote install'
        env = dict(self.env, CCPROFILE_INSTALL_DIR=str(install),
                   SOURCE_DIR=str(ROOT / 'src'))
        installer = (ROOT / 'install.sh').read_text()
        result = subprocess.run(['sh'], input=installer, env=env, cwd=self.home,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        artifact = install / 'ccprofile'
        expected = ((ROOT / 'src' / 'launcher.sh').read_bytes()
                    + (ROOT / 'src' / 'ccprofile.py').read_bytes())
        self.assertEqual(artifact.read_bytes(), expected)
        result = subprocess.run(['sh', str(artifact), '--version'], env=env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('ccprofile 0.1.0', result.stdout)
        result = subprocess.run(['sh'], input=installer,
                                env=dict(env, FAIL_DOWNLOAD='1'), cwd=self.home,
                                capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(artifact.read_bytes(), expected)
        self.assertEqual(list(install.iterdir()), [artifact])


if __name__ == '__main__':
    unittest.main()
