"""Offline regressions for the authentication, settings and input review findings."""
import contextlib
import errno
import io
import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import stat
import subprocess
import termios
import time
import unittest
from unittest import mock

import test_ccprofile as existing

cc = existing.cc
CLI = existing.CLI


class ReviewRegressionTests(unittest.TestCase):
    # Reuse the isolated HOME and fake executable without inheriting its tests.
    setUp = existing.ProfileTests.setUp
    invoke = existing.ProfileTests.invoke
    add_api = existing.ProfileTests.add_api
    record = existing.ProfileTests.record

    def test_external_profile_selection_and_default_namespace(self):
        # This models external active/default profile lookup and the distinction
        # between explicit and implicit Claude credential namespaces. No OAuth
        # data, platform Keychain, real account, or network is accessed.
        fake = r'''#!/usr/bin/env python3
import json, os
from pathlib import Path
home = Path(os.environ['HOME'])
claude = os.environ.get('CLAUDE_CONFIG_DIR')
anthropic = Path(os.environ.get('ANTHROPIC_CONFIG_DIR', str(home / '.config' / 'anthropic')))
selector = anthropic / 'fake-active-profile'
record = {'env': dict(os.environ),
          'credential_namespace': ('explicit:' + claude) if claude is not None else 'standard-default',
          'external_profile': selector.read_text() if selector.exists() else None,
          'anthropic_directory': str(anthropic)}
Path(os.environ['FAKE_OUTPUT']).write_text(json.dumps(record))
'''
        (self.fakebin / 'claude').write_text(fake)
        (self.home / '.claude').mkdir()
        external = self.home / '.config' / 'anthropic'
        external.mkdir(parents=True)
        selector = external / 'fake-active-profile'
        selector.write_text('NONSECRET_GLOBAL_PROFILE')
        self.invoke('add', 'a', '--type', 'claude')
        self.invoke('add', 'b', '--type', 'claude')
        self.invoke('add', 'default', '--type', 'claude', '--use-default-config')
        conflicting = {'CLAUDE_CONFIG_DIR': '/parent/claude',
                       'ANTHROPIC_CONFIG_DIR': '/parent/anthropic',
                       'ANTHROPIC_PROFILE': 'NONSECRET_PARENT_PROFILE'}
        original = dict(conflicting)
        records = []
        for name in ('a', 'b'):
            self.invoke('run', name, extra=conflicting)
            record = self.record()
            records.append(record)
            self.assertIsNone(record['external_profile'])
            self.assertNotIn('ANTHROPIC_PROFILE', record['env'])
            claude_dir = Path(record['env']['CLAUDE_CONFIG_DIR'])
            auth_dir = Path(record['env']['ANTHROPIC_CONFIG_DIR'])
            self.assertEqual(auth_dir, claude_dir / 'anthropic')
            for directory in (claude_dir, auth_dir):
                self.assertFalse(directory.is_symlink())
                self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertNotEqual(records[0]['credential_namespace'], records[1]['credential_namespace'])
        self.assertNotEqual(records[0]['anthropic_directory'], records[1]['anthropic_directory'])
        # A selector in A's dedicated namespace must not become B's selector.
        (Path(records[0]['anthropic_directory']) / 'fake-active-profile').write_text('NONSECRET_A_PROFILE')
        self.invoke('run', 'a', extra=conflicting)
        self.assertEqual(self.record()['external_profile'], 'NONSECRET_A_PROFILE')
        self.invoke('run', 'b', extra=conflicting)
        self.assertIsNone(self.record()['external_profile'])
        self.invoke('run', 'default', extra=conflicting)
        record = self.record()
        self.assertEqual(record['credential_namespace'], 'standard-default')
        self.assertEqual(record['external_profile'], 'NONSECRET_GLOBAL_PROFILE')
        self.assertEqual(record['anthropic_directory'], str(external))
        self.assertNotIn('CLAUDE_CONFIG_DIR', record['env'])
        self.assertNotIn('ANTHROPIC_CONFIG_DIR', record['env'])
        self.assertEqual(conflicting, original)
        self.assertEqual(selector.read_text(), 'NONSECRET_GLOBAL_PROFILE')
        profile = self.store.load()['profiles']['default']
        source = dict(self.env, **conflicting)
        unchanged = dict(source)
        _, child = cc.build_launch(profile, self.store.config_dir(profile), [], source)
        self.assertEqual(source, unchanged)
        self.assertNotIn('CLAUDE_CONFIG_DIR', child)
        self.assertNotIn('ANTHROPIC_CONFIG_DIR', child)

    def test_api_also_uses_private_anthropic_directory_and_refuses_symlink(self):
        self.add_api()
        self.invoke('run', 'gateway', extra={'CCPROFILE_TOKEN': existing.FAKE_TOKEN})
        child = self.record()['env']
        auth_dir = Path(child['ANTHROPIC_CONFIG_DIR'])
        self.assertEqual(auth_dir, Path(child['CLAUDE_CONFIG_DIR']) / 'anthropic')
        self.assertEqual(stat.S_IMODE(auth_dir.stat().st_mode), 0o700)
        auth_dir.rmdir()
        target = self.home / 'outside-auth'
        target.mkdir(mode=0o700)
        auth_dir.symlink_to(target, target_is_directory=True)
        self.output.unlink()
        self.invoke('run', 'gateway', extra={'CCPROFILE_TOKEN': existing.FAKE_TOKEN}, expected=1)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(target.iterdir()), [])

    def _git(self, cwd, *args):
        result = subprocess.run(['git', *args], cwd=cwd, env=self.env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def _from(self, cwd, *args, expected):
        result = subprocess.run([str(CLI), *args], cwd=cwd,
                                env=dict(self.env, CCPROFILE_TOKEN=existing.FAKE_TOKEN),
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertNotIn(existing.FAKE_TOKEN, result.stdout + result.stderr)
        return result

    @unittest.skipUnless(shutil.which('git'), 'git is needed for the disposable worktree fixture')
    def test_worktree_main_checkout_settings_block_run_and_doctor(self):
        self.add_api()
        main = self.home / 'main-checkout'
        main.mkdir()
        self._git(main, 'init', '-q')
        self._git(main, '-c', 'user.name=Offline Test', '-c', 'user.email=offline@example.invalid',
                  'commit', '-q', '--allow-empty', '-m', 'Disposable fixture')
        worktree = self.home / 'sibling-worktree'
        self._git(main, 'worktree', 'add', '--detach', str(worktree))
        launch = worktree / 'nested' / 'directory'
        launch.mkdir(parents=True)
        local = main / '.claude' / 'settings.local.json'
        local.parent.mkdir()
        local.write_text(json.dumps({'env': {'ANTHROPIC_BASE_URL': 'https://nonsecret.invalid'}}))
        directory = self.store.ensure_dir(self.store.load()['profiles']['gateway'])
        self.assertIn(local.resolve(), [path.resolve() for path in cc.settings_paths(directory, launch)])
        self._from(launch, 'run', 'gateway', expected=1)
        self.assertFalse(self.output.exists())
        doctor = self._from(launch, 'doctor', 'gateway', expected=1)
        self.assertIn('settings conflict', doctor.stdout)
        self.assertIn(str(local.resolve()), doctor.stdout)
        self.assertNotIn('https://nonsecret.invalid', doctor.stdout)
        # Git discovery and diagnostics must not mutate repository metadata.
        before = (main / '.git' / 'config').read_bytes()
        local.unlink()
        self._from(launch, 'run', 'gateway', expected=0)
        self.assertEqual((main / '.git' / 'config').read_bytes(), before)

    @unittest.skipUnless(shutil.which('git'), 'git is needed for the separate git-dir fixture')
    def test_separate_git_dir_uses_checkout_settings(self):
        self.add_api()
        checkout = self.home / 'separate-checkout'
        checkout.mkdir()
        self._git(checkout, 'init', '-q', '--separate-git-dir', str(self.home / 'metadata'))
        local = checkout / '.claude' / 'settings.local.json'
        local.parent.mkdir()
        local.write_text(json.dumps({'apiKeyHelper': 'NONSECRET_CONFLICT'}))
        launch = checkout / 'deep' / 'cwd'
        launch.mkdir(parents=True)
        directory = self.store.ensure_dir(self.store.load()['profiles']['gateway'])
        self.assertIn(local, cc.settings_paths(directory, launch))
        self._from(launch, 'run', 'gateway', expected=1)
        self.assertFalse(self.output.exists())

    def test_no_git_still_checks_home_ancestor_settings(self):
        self.add_api()
        launch = self.home / 'project' / 'nested'
        launch.mkdir(parents=True)
        local = self.home / '.claude' / 'settings.local.json'
        local.parent.mkdir()
        local.write_text(json.dumps({'env': {'ANTHROPIC_AUTH_TOKEN': 'NONSECRET_ANCESTOR'}}))
        directory = self.store.ensure_dir(self.store.load()['profiles']['gateway'])
        with mock.patch.object(cc.shutil, 'which', return_value=None):
            self.assertIn(local, cc.settings_paths(directory, launch))
        self._from(launch, 'run', 'gateway', expected=1)
        self.assertFalse(self.output.exists())
        local.unlink()
        # Remove git from PATH while retaining the isolated fake Claude/python.
        (self.fakebin / 'python3').symlink_to(os.sys.executable)
        result = subprocess.run([str(CLI), 'run', 'gateway'], cwd=launch,
                                env=dict(self.env, PATH=str(self.fakebin), CCPROFILE_TOKEN=existing.FAKE_TOKEN),
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.output.exists())

    @unittest.skipUnless(shutil.which('git'), 'git is needed to create a disposable linked tree')
    def test_worktree_without_git_refuses_unverified_settings_before_launch(self):
        self.add_api()
        main = self.home / 'git-main'
        main.mkdir()
        self._git(main, 'init', '-q')
        self._git(main, '-c', 'user.name=Offline Test', '-c', 'user.email=offline@example.invalid',
                  'commit', '-q', '--allow-empty', '-m', 'Disposable fixture')
        worktree = self.home / 'git-linked'
        self._git(main, 'worktree', 'add', '--detach', str(worktree))
        (self.fakebin / 'python3').symlink_to(os.sys.executable)
        env = dict(self.env, PATH=str(self.fakebin), CCPROFILE_TOKEN=existing.FAKE_TOKEN)
        for command in ('run', 'doctor'):
            with self.subTest(command=command):
                result = subprocess.run([str(CLI), command, 'gateway'], cwd=worktree, env=env,
                                        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn('git is required', result.stdout + result.stderr)
                self.assertNotIn(existing.FAKE_TOKEN, result.stdout + result.stderr)
                self.assertFalse(self.output.exists())

    def _terminal_run(self, payload=None, interrupt=None, control_c=False):
        """Feed a controlling PTY with bounded, partial-write-safe I/O."""
        self.output.unlink(missing_ok=True)
        gate_read, gate_write = os.pipe()
        pid, master = pty.fork()
        if pid == 0:
            os.close(gate_write)
            os.read(gate_read, 1)
            os.close(gate_read)
            os.chdir(self.home)
            os.execve(str(CLI), [str(CLI), 'run', 'gateway'], self.env)
        os.close(gate_read)
        baseline = termios.tcgetattr(master)
        os.write(gate_write, b'x')
        os.close(gate_write)
        os.set_blocking(master, False)
        captured = bytearray()
        status = None

        def drain():
            try:
                captured.extend(os.read(master, 8192))
            except OSError as error:
                if error.errno not in (errno.EIO, errno.EAGAIN, errno.EWOULDBLOCK):
                    raise

        try:
            deadline = time.monotonic() + 10
            while b'API token (not saved): ' not in captured:
                if time.monotonic() >= deadline:
                    self.fail('token prompt did not appear')
                if select.select([master], [], [], 0.05)[0]:
                    drain()
                found, waited = os.waitpid(pid, os.WNOHANG)
                if found:
                    status = waited
                    self.fail('terminal child exited before its prompt')
            active = termios.tcgetattr(master)
            self.assertFalse(active[3] & termios.ECHO)
            self.assertFalse(active[3] & termios.ICANON)
            if interrupt is not None:
                os.kill(pid, interrupt)
            data = b'\x03' if control_c else payload or b''
            sent = 0
            deadline = time.monotonic() + 15
            while status is None and time.monotonic() < deadline:
                readable, writable, _ = select.select([master], [master] if sent < len(data) else [], [], 0.05)
                if readable:
                    drain()
                if writable:
                    try:
                        # Do not assume a 65 KiB write fits in the PTY buffer.
                        sent += os.write(master, data[sent:sent + 4096])
                    except OSError as error:
                        if error.errno not in (errno.EIO, errno.EAGAIN, errno.EWOULDBLOCK):
                            raise
                found, waited = os.waitpid(pid, os.WNOHANG)
                if found:
                    status = waited
            self.assertIsNotNone(status, 'terminal child did not finish before deadline')
            while select.select([master], [], [], 0)[0]:
                length = len(captured)
                drain()
                if len(captured) == length:
                    break
            self.assertEqual(termios.tcgetattr(master), baseline,
                             'all terminal attributes, including control characters, must be restored')
            return os.waitstatus_to_exitcode(status), bytes(captured)
        finally:
            if status is None:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            os.close(master)

    def test_terminal_preserves_long_tokens_exactly(self):
        self.add_api()
        for length in (5000, 65536):
            with self.subTest(length=length):
                token = ('Ab9_' * ((length + 3) // 4))[:length]
                code, captured = self._terminal_run((token + '\n').encode('ascii'))
                self.assertEqual(code, 0)
                self.assertTrue(token.encode()[:32] not in captured, 'token was echoed')
                actual = self.record()['env']['ANTHROPIC_AUTH_TOKEN']
                self.assertTrue(actual == token, 'fake client did not receive the complete token')

    def test_terminal_rejects_invalid_tokens_without_launch_or_echo(self):
        self.add_api()
        cases = [('overlength', b'T' * 65537), ('nonascii', 'NONSECRET_\u00e9'.encode('utf-8')),
                 ('whitespace', b'NONSECRET token'), ('empty', b'')]
        for label, token in cases:
            with self.subTest(case=label):
                code, captured = self._terminal_run(token + b'\n')
                self.assertEqual(code, 1)
                self.assertFalse(self.output.exists())
                if token:
                    self.assertTrue(token[:32] not in captured, 'invalid token was echoed')

    def test_terminal_restores_all_attributes_on_signals_and_ctrl_c(self):
        self.add_api()
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=sig.name):
                code, _ = self._terminal_run(interrupt=sig)
                self.assertEqual(code, 128 + sig)
                self.assertFalse(self.output.exists())
        code, _ = self._terminal_run(control_c=True)
        self.assertEqual(code, 130)
        self.assertFalse(self.output.exists())

    def test_terminal_editing_and_eof_cancellation(self):
        self.add_api()
        token = b'NONSECRET_EDITED_TOKEN'
        for payload in (b'wrong' + b'\x7f' * 5 + token + b'\n',
                        b'T' * 65537 + b'\x15' + token + b'\n'):
            code, captured = self._terminal_run(payload)
            self.assertEqual(code, 0)
            self.assertTrue(token not in captured, 'edited token was echoed')
            self.assertTrue(self.record()['env']['ANTHROPIC_AUTH_TOKEN'] == token.decode(),
                            'terminal editing changed the selected token')
        code, _ = self._terminal_run(b'\x04')
        self.assertEqual(code, 1)
        self.assertFalse(self.output.exists())

    def test_environment_token_boundaries(self):
        self.add_api()
        for length in (5000, 65536):
            with self.subTest(length=length):
                token = 'ENV_NONSECRET_' + 'A' * (length - len('ENV_NONSECRET_'))
                result = self.invoke('run', 'gateway', extra={'CCPROFILE_TOKEN': token})
                self.assertTrue(token not in result.stdout + result.stderr, 'token appeared in output')
                self.assertTrue(self.record()['env']['ANTHROPIC_AUTH_TOKEN'] == token,
                                'environment token changed before launch')
        for label, token in [('overlength', 'A' * 65537), ('empty', ''),
                             ('whitespace', 'NONSECRET token'), ('nonascii', 'NONSECRET_\u00e9')]:
            with self.subTest(case=label):
                self.output.unlink(missing_ok=True)
                result = self.invoke('run', 'gateway', extra={'CCPROFILE_TOKEN': token}, expected=1)
                self.assertFalse(self.output.exists())
                if token:
                    self.assertTrue(token not in result.stdout + result.stderr, 'invalid token appeared in output')

    def _first_doctor(self):
        output = io.StringIO()
        with mock.patch.object(cc.sys, 'platform', 'darwin'), \
                mock.patch.object(cc.shutil, 'which', return_value=str(self.fakebin / 'claude')), \
                contextlib.redirect_stdout(output), mock.patch.dict(os.environ, self.env, clear=True):
            code = cc.diagnostic(self.store, None)
        return code, output.getvalue()

    def test_empty_doctor_creates_private_root_and_removes_write_probe(self):
        self.assertFalse(self.store.root.exists())
        with mock.patch.object(cc.tempfile, 'mkstemp', wraps=cc.tempfile.mkstemp) as probe:
            code, output = self._first_doctor()
        self.assertEqual(code, 0, output)
        self.assertTrue(self.store.root.is_dir())
        self.assertEqual(stat.S_IMODE(self.store.root.stat().st_mode), 0o700)
        self.assertFalse(self.store.path.exists())
        self.assertEqual(list(self.store.root.iterdir()), [])
        self.assertTrue(any(Path(call.kwargs['dir']) == self.store.root for call in probe.call_args_list),
                        'doctor did not probe the management root')

    def test_empty_doctor_refuses_config_parent_that_is_a_regular_file(self):
        (self.home / '.config').write_text('NONSECRET_UNUSABLE_PARENT')
        code, output = self._first_doctor()
        self.assertEqual(code, 1)
        self.assertIn('FAIL ', output)
        self.assertNotIn('NONSECRET_UNUSABLE_PARENT', output)
        self.assertFalse(self.store.root.exists())

    @unittest.skipIf(os.getuid() == 0, 'root bypasses ordinary filesystem write permissions')
    def test_empty_doctor_refuses_unwritable_config_parent(self):
        parent = self.home / '.config'
        parent.mkdir(mode=0o500)
        self.addCleanup(parent.chmod, 0o700)
        code, output = self._first_doctor()
        self.assertEqual(code, 1)
        self.assertIn('FAIL ', output)
        self.assertFalse(self.store.root.exists())

    def test_empty_doctor_reports_write_probe_failure_safely(self):
        with mock.patch.object(cc.tempfile, 'mkstemp', side_effect=OSError('NONSECRET_FAULT_DETAIL')) as probe:
            code, output = self._first_doctor()
        self.assertTrue(probe.called, 'doctor did not attempt a first-use write probe')
        self.assertEqual(code, 1)
        self.assertIn('FAIL ', output)
        self.assertNotIn('NONSECRET_FAULT_DETAIL', output)
        self.assertEqual(list(self.store.root.iterdir()), [])

    def test_empty_doctor_reports_data_write_failure_and_removes_probe(self):
        with mock.patch.object(cc.os, 'write', side_effect=OSError('NONSECRET_WRITE_FAULT')):
            code, output = self._first_doctor()
        self.assertEqual(code, 1)
        self.assertIn('FAIL ', output)
        self.assertNotIn('NONSECRET_WRITE_FAULT', output)
        self.assertEqual(list(self.store.root.iterdir()), [])

    def test_dot_segment_urls_rejected_on_add_and_edit_without_changing_config(self):
        self.add_api()
        original = self.store.path.read_bytes()
        paths = ('/messages/.', '/messages/%2e', '/messages/%2E',
                 '/v1/messages/.', '/v1/messages/%2e/', '/other/../messages',
                 '/other/%2e%2e/messages', '/messages/child/..', '/messages/child/.%2E')
        for path in paths:
            with self.subTest(path=path):
                url = 'https://example.com' + path
                self.invoke('add', 'invalid', '--type', 'api', '--base-url', url,
                            '--model', 'model-v1', '--auth', 'bearer', expected=1)
                self.assertEqual(self.store.path.read_bytes(), original)
                self.invoke('edit', 'gateway', '--base-url', url, expected=1)
                self.assertEqual(self.store.path.read_bytes(), original)
        for suffix in ('/api', '/v1'):
            url = 'https://example.com' + suffix
            self.invoke('edit', 'gateway', '--base-url', url)
            self.assertEqual(self.store.load()['profiles']['gateway']['base_url'], url)


if __name__ == '__main__':
    unittest.main()
