import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import termios
import urllib.parse

try:
    import tomllib
except ImportError:
    print('ccprofile: Python 3.11 or later is required (brew install python).', file=sys.stderr)
    sys.exit(1)

VERSION = '0.1.0'
NAME = re.compile(r'[a-zA-Z][a-zA-Z0-9_-]{0,63}\Z')
ID = re.compile(r'[a-f0-9]{32}\Z')
MAX_FILE = 1024 * 1024
MAX_TOKEN = 65536
MAX_CONTEXT_WINDOW = 2**53 - 1  # Largest exact integer in Claude Code's JavaScript runtime.
ALLOWED_CLAUDE = frozenset({
    'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC',
    'CLAUDE_CODE_DISABLE_TERMINAL_TITLE', 'CLAUDE_CODE_ACCESSIBILITY',
    'CLAUDE_CODE_SYNTAX_HIGHLIGHT', 'CLAUDE_CODE_MAX_OUTPUT_TOKENS',
})
PROVIDER_PREFIXES = ('ANTHROPIC_', 'CLAUDE_', 'AWS_', 'AZURE_',
                     'GOOGLE_', 'GCLOUD_', 'GCP_', 'VERTEX_', 'BEDROCK_', 'CCPROFILE_')


class SafeError(Exception):
    """Only fixed, non-secret messages may be supplied to this exception."""


class PromptInterrupted(Exception):
    def __init__(self, signum):
        self.signum = signum


def managed_env(key):
    return key not in ALLOWED_CLAUDE and (key == 'CLAUDECODE' or key.startswith(PROVIDER_PREFIXES))


def validate_url(value):
    try:
        u = urllib.parse.urlsplit(value)
        valid = (value.startswith('https://') and u.scheme == 'https' and u.hostname
                 and u.username is None and u.password is None and u.port != 0
                 and '?' not in value and '#' not in value
                 and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
                 and not any(c in u.netloc for c in '\\%"'))
        if not valid:
            raise ValueError
        path = urllib.parse.unquote(u.path)
        if any(part in ('.', '..') for part in path.split('/')):
            raise SafeError('base URL must not contain dot path segments')
        if path.rstrip('/').endswith('/messages'):
            raise SafeError('base URL must not include the /messages API path')
    except (ValueError, TypeError):
        raise SafeError('base URL must be HTTPS without credentials, query, or fragment') from None


def validate_profile(name, p):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise SafeError('profile name must match ^[a-zA-Z][a-zA-Z0-9_-]{0,63}$')
    if not isinstance(p, dict) or set(p) - {'id', 'type', 'base_url', 'model', 'auth', 'use_default_config', 'context_window'}:
        raise SafeError('invalid profile fields')
    if not isinstance(p.get('id'), str) or not ID.fullmatch(p['id']):
        raise SafeError('invalid internal profile ID')
    if p.get('type') == 'claude':
        if any(k in p for k in ('base_url', 'model', 'auth', 'context_window')):
            raise SafeError('claude profiles cannot contain API settings')
        if type(p.get('use_default_config', False)) is not bool:
            raise SafeError('invalid default config flag')
    elif p.get('type') == 'api':
        if 'use_default_config' in p:
            raise SafeError('only claude profiles can use ~/.claude')
        if not isinstance(p.get('base_url'), str):
            raise SafeError('API base URL is required')
        validate_url(p['base_url'])
        model = p.get('model')
        if (not isinstance(model, str) or not model or model.startswith('-')
                or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in model)):
            raise SafeError('a model ID without whitespace or control characters is required')
        if p.get('auth') not in ('bearer', 'x-api-key'):
            raise SafeError('auth must be bearer or x-api-key')
        if 'context_window' in p:
            window = p['context_window']
            if type(window) is not int or not 1 <= window <= MAX_CONTEXT_WINDOW:
                raise SafeError('context window must be a positive integer no greater than 9007199254740991')
    else:
        raise SafeError('type must be claude or api')


def validate_config(c):
    if not isinstance(c, dict) or set(c) - {'version', 'profiles'} or type(c.get('version')) is not int or c['version'] != 1:
        raise SafeError('invalid configuration or unsupported version')
    if not isinstance(c.get('profiles'), dict):
        raise SafeError('invalid profiles table')
    ids = set()
    defaults = 0
    for name, p in c['profiles'].items():
        validate_profile(name, p)
        if p['id'] in ids:
            raise SafeError('duplicate internal profile IDs')
        ids.add(p['id'])
        defaults += p.get('use_default_config', False)
    if defaults > 1:
        raise SafeError('only one profile may use ~/.claude')


def private_dir(path, create=False):
    if create:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.getuid():
        raise SafeError('managed directories must be owned by you, real directories, and mode 0700')


def read_private(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        info = os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
            raise SafeError('config must be owned by you, a regular file, and mode 0600')
        data = f.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            raise SafeError('config exceeds the 1 MiB size limit')
        return data


class Store:
    def __init__(self, home=None):
        self.home = Path(home) if home is not None else Path.home()
        self.root = self.home / '.config' / 'ccprofile'
        self.path = self.root / 'config.toml'

    def load(self):
        c = {'version': 1, 'profiles': {}}
        if not self.root.exists() and not self.root.is_symlink():
            return c
        private_dir(self.root)
        try:
            data = read_private(self.path)
        except FileNotFoundError:
            return c
        try:
            c = tomllib.loads(data.decode('utf-8'))
        except (ValueError, UnicodeError):
            raise SafeError('invalid TOML configuration; parser details are suppressed') from None
        c.setdefault('profiles', {})
        validate_config(c)
        return c

    def prepare(self):
        self.root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        private_dir(self.root, True)

    @contextlib.contextmanager
    def lock(self):
        self.prepare()
        fd = os.open(self.root / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(fd, 'rb') as f:
            info = os.fstat(f.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
                raise SafeError('unsafe configuration lock')
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def save(self, c):
        validate_config(c)
        lines = ['version = 1', '']
        for name, p in sorted(c['profiles'].items()):
            lines += ['[profiles.' + name + ']']
            for key, value in p.items():
                text = ('true' if value else 'false') if type(value) is bool else json.dumps(value, ensure_ascii=False)
                lines.append(key + ' = ' + text)
            lines.append('')
        data = ('\n'.join(lines)).encode('utf-8')
        if len(data) > MAX_FILE:
            raise SafeError('config exceeds the 1 MiB size limit')
        fd, tmp = tempfile.mkstemp(prefix='.config-', dir=self.root)
        try:
            with os.fdopen(fd, 'wb') as f:
                os.fchmod(f.fileno(), 0o600)
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            # The rename has committed. Do not report rollback if directory fsync
            # is unsupported by the filesystem.
            with contextlib.suppress(OSError):
                d = os.open(self.root, os.O_RDONLY)
                try:
                    os.fsync(d)
                finally:
                    os.close(d)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)

    def update(self, change):
        with self.lock():
            c = self.load()
            result = change(c)
            self.save(c)
            return result

    def config_dir(self, p):
        if p.get('use_default_config'):
            return self.home / '.claude'
        return self.root / 'profiles' / p['id']

    def anthropic_dir(self, p):
        return None if p.get('use_default_config') else self.config_dir(p) / 'anthropic'

    def ensure_dir(self, p):
        path = self.config_dir(p)
        if p.get('use_default_config'):
            if not path.is_dir():
                raise SafeError('~/.claude must already exist as a directory')
            return path
        self.prepare()
        private_dir(self.root / 'profiles', True)
        private_dir(path, True)
        private_dir(self.anthropic_dir(p), True)
        return path


def find_profile(c, name):
    if not NAME.fullmatch(name):
        raise SafeError('invalid profile name')
    if name not in c['profiles']:
        raise SafeError('profile does not exist')
    return c['profiles'][name]


def validate_token(token):
    if not token or len(token) > MAX_TOKEN or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise SafeError('token must contain 1–65536 printable ASCII characters without whitespace')
    return token


def read_token(env):
    if 'CCPROFILE_TOKEN' in env:
        return validate_token(env['CCPROFILE_TOKEN'])
    # Noncanonical input avoids the terminal driver's canonical line limit.
    # Keep echo disabled until the entire line has been consumed, even on error.
    fd = None
    try:
        fd = os.open('/dev/tty', os.O_RDWR | os.O_NOCTTY)
        if not os.isatty(fd):
            raise OSError
        original = termios.tcgetattr(fd)
        hidden = original[:]
        hidden[6] = original[6][:]
        hidden[0] &= ~(termios.ICRNL | termios.INLCR | termios.IGNCR
                       | termios.IXON | termios.IXOFF | termios.ISTRIP)
        hidden[3] &= ~(termios.ECHO | termios.ECHONL | termios.ICANON)
        hidden[6][termios.VMIN] = 1
        hidden[6][termios.VTIME] = 0
        erase = original[6][termios.VERASE]
        erase = erase if isinstance(erase, int) else erase[0]
        def interrupted(signum, frame):
            raise PromptInterrupted(signum)
        previous = {}
        try:
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                previous[sig] = signal.signal(sig, interrupted)
            termios.tcsetattr(fd, termios.TCSANOW, hidden)
            os.write(fd, b'API token (not saved): ')
            token = bytearray()
            overflow = 0
            while True:
                char = os.read(fd, 1)
                if not char or char == b'\x04':
                    raise SafeError('API token input cancelled')
                if char in (b'\r', b'\n'):
                    break
                value = char[0]
                if value in (8, 127, erase):
                    if overflow:
                        overflow -= 1
                    elif token:
                        token.pop()
                elif value == 21:  # Ctrl-U clears the current input line.
                    token.clear()
                    overflow = 0
                elif len(token) < MAX_TOKEN:
                    token.append(value)
                else:
                    overflow += 1
            os.write(fd, b'\n')
        finally:
            try:
                # Discard queued paste suffixes before restoring echo or exec.
                termios.tcflush(fd, termios.TCIFLUSH)
            finally:
                try:
                    termios.tcsetattr(fd, termios.TCSANOW, original)
                finally:
                    for sig, handler in previous.items():
                        signal.signal(sig, handler)
    except (OSError, termios.error):
        raise SafeError('API token is required; use a terminal or set CCPROFILE_TOKEN for this invocation') from None
    finally:
        if fd is not None:
            os.close(fd)
    if overflow:
        raise SafeError('token must contain 1–65536 printable ASCII characters without whitespace')
    return validate_token(token.decode('latin-1'))


def build_launch(p, directory, args, env, token=None):
    child_args = list(args)
    if p['type'] == 'api':
        if any(a == '--model' or a.startswith('--model=') for a in args):
            raise SafeError('--model conflicts with the API profile; use ccprofile edit NAME --model MODEL')
        child_args = ['--model', p['model']] + child_args
    child_env = {k: v for k, v in env.items() if not managed_env(k)}
    if not p.get('use_default_config'):
        child_env['CLAUDE_CONFIG_DIR'] = str(directory)
        child_env['ANTHROPIC_CONFIG_DIR'] = str(directory / 'anthropic')
    if p['type'] == 'api':
        child_env['ANTHROPIC_BASE_URL'] = p['base_url']
        child_env['ANTHROPIC_AUTH_TOKEN' if p['auth'] == 'bearer' else 'ANTHROPIC_API_KEY'] = validate_token(token or '')
        if 'context_window' in p:
            child_env['CLAUDE_CODE_MAX_CONTEXT_TOKENS'] = str(p['context_window'])
    return child_args, child_env


def settings_conflicts(path, api=True):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except FileNotFoundError:
        return []
    with os.fdopen(fd, 'rb') as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise SafeError('settings must be regular files')
        data = f.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise SafeError('settings exceed the 1 MiB size limit')
    try:
        settings = json.loads(data)
        if not isinstance(settings, dict) or ('env' in settings and not isinstance(settings['env'], dict)):
            raise ValueError
    except (ValueError, UnicodeError):
        raise SafeError('invalid settings JSON; values are not displayed') from None
    conflicts = []
    for key in ('apiKeyHelper', 'awsAuthRefresh', 'awsCredentialExport', 'forceLoginMethod', 'forceLoginOrgUUID'):
        if key in settings:
            conflicts.append(key)
    if api and 'model' in settings:
        conflicts.append('model')
    if any(managed_env(k) for k in settings.get('env', {})):
        conflicts.append('managed environment variable')
    return conflicts


def _main_worktree_settings(cwd):
    # These metadata commands neither run hooks nor read authentication files.
    # Parent GIT_* overrides must not redirect discovery to another repository.
    git = shutil.which('git')
    if os.name == 'nt':
        return None
    if git is None:
        # A .git file can point to a linked tree whose main settings are outside
        # the ancestor chain. Do not silently skip that inspection before auth.
        if any((parent / '.git').is_file() or (parent / '.git').is_symlink()
               for parent in (cwd, *cwd.parents)):
            raise SafeError('git is required to inspect linked-worktree settings safely')
        return None
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    failure = 'cannot resolve Git worktree settings safely; check repository metadata and ownership'

    def query(where, *args):
        try:
            result = subprocess.run([git, '--no-optional-locks', '-C', str(where), *args],
                                    env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise SafeError(failure) from None
        if len(result.stdout) > MAX_FILE:
            raise SafeError(failure)
        return result

    def output_path(result, relative_to):
        if result.returncode or not result.stdout.endswith(b'\n'):
            raise SafeError(failure)
        value = os.fsdecode(result.stdout[:-1])
        if not value or '\x00' in value:
            raise SafeError(failure)
        path = Path(value)
        return (path if path.is_absolute() else relative_to / path).resolve()

    cwd = cwd.resolve()
    root_query = query(cwd, 'rev-parse', '--show-toplevel')
    if root_query.returncode:
        bare = query(cwd, 'rev-parse', '--is-bare-repository')
        if bare.returncode == 0 and bare.stdout == b'true\n':
            return None  # A bare repository has no main checkout local settings.
        if any((parent / '.git').exists() or (parent / '.git').is_symlink()
               or ((parent / 'HEAD').is_file() and (parent / 'objects').is_dir())
               for parent in (cwd, *cwd.parents)):
            raise SafeError(failure)
        return None
    root = output_path(root_query, cwd)
    worktrees = query(cwd, 'worktree', 'list', '--porcelain', '-z')
    if worktrees.returncode:
        raise SafeError(failure)
    first = worktrees.stdout.split(b'\x00\x00', 1)[0].split(b'\x00')
    if not first or not first[0].startswith(b'worktree '):
        raise SafeError(failure)
    if b'bare' in first:
        return None
    main = Path(os.fsdecode(first[0][len(b'worktree '):]))
    if not main.is_absolute():
        raise SafeError(failure)
    main = main.resolve()
    try:
        info = main.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise SafeError(failure)
        main_query = query(main, 'rev-parse', '--show-toplevel')
        if main_query.returncode == 0 and output_path(main_query, main) == main:
            return main / '.claude' / 'settings.local.json'
        # With --separate-git-dir, Git can list the metadata directory instead
        # of the main checkout. The current checkout is authoritative only when
        # its Git directory is the common directory (it is not a linked tree).
        git_dir = output_path(query(cwd, 'rev-parse', '--absolute-git-dir'), cwd)
        common = output_path(query(cwd, 'rev-parse', '--git-common-dir'), cwd)
        if git_dir == common == main and root.stat().st_uid == os.getuid():
            return root / '.claude' / 'settings.local.json'
    except OSError:
        raise SafeError(failure) from None
    raise SafeError(failure)


def settings_paths(directory, cwd=None):
    cwd = Path.cwd() if cwd is None else Path(cwd)
    paths = [directory / 'settings.json', directory / 'settings.local.json']
    for parent in (cwd, *cwd.parents):
        paths.extend([parent / '.claude' / 'settings.json', parent / '.claude' / 'settings.local.json'])
    main_local = _main_worktree_settings(cwd)
    if main_local is not None:
        # Preserve ancestor checks, including HOME and repositories at HOME.
        paths.append(main_local)
    if sys.platform == 'darwin':
        managed = Path('/Library/Application Support/ClaudeCode')
        paths.append(managed / 'managed-settings.json')
        fragments = managed / 'managed-settings.d'
        if fragments.exists():
            paths.extend(sorted(fragments.glob('*.json')))
    return list(dict.fromkeys(paths))


def check_settings(directory, api):
    for path in settings_paths(directory):
        conflicts = settings_conflicts(path, api)
        if conflicts:
            raise SafeError('conflicting Claude settings found; run ccprofile doctor NAME')


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse messages can contain supplied values. Do not quote them.
        self.exit(2, 'ccprofile: invalid arguments; use --help for the command\n')


def parser():
    p = Parser(prog='ccprofile', description='Launch Claude Code with an isolated named profile. Tokens are never saved.')
    p.add_argument('--version', action='version', version='ccprofile ' + VERSION)
    commands = p.add_subparsers(dest='command', required=True)
    add = commands.add_parser('add', help='register a profile without authenticating')
    add.add_argument('name')
    add.add_argument('--type', required=True, choices=('claude', 'api'))
    add.add_argument('--base-url')
    add.add_argument('--model')
    add.add_argument('--auth', choices=('bearer', 'x-api-key'),
                     help='API authentication method (default: bearer)')
    add.add_argument('--context-window', type=int, metavar='TOKENS',
                     help='context window to declare to Claude Code for an API profile')
    add.add_argument('--use-default-config', action='store_true')
    commands.add_parser('list', help='list non-secret profile settings')
    for name in ('show', 'remove', 'login'):
        cmd = commands.add_parser(name)
        cmd.add_argument('name')
    edit = commands.add_parser('edit', help='change an API profile')
    edit.add_argument('name')
    edit.add_argument('--base-url')
    edit.add_argument('--model')
    edit.add_argument('--auth', choices=('bearer', 'x-api-key'),
                      help='API authentication method (default: keep current value)')
    context = edit.add_mutually_exclusive_group()
    context.add_argument('--context-window', type=int, metavar='TOKENS',
                         help='context window to declare to Claude Code (default: keep current value)')
    context.add_argument('--clear-context-window', action='store_true',
                         help='remove the context window override')
    run = commands.add_parser('run', help='run NAME [-- CLAUDE_ARGS...]')
    run.add_argument('name')
    doctor = commands.add_parser('doctor', help='check local configuration without sending API requests')
    doctor.add_argument('name', nargs='?')
    return p


def _probe_writable(directory):
    fd, path = tempfile.mkstemp(prefix='.ccprofile-check-', dir=directory)
    try:
        os.write(fd, b'\x00')
        os.fsync(fd)
    finally:
        try:
            os.close(fd)
        finally:
            os.unlink(path)


def diagnostic(store, name):
    ok = True
    def report(success, label, detail=''):
        nonlocal ok
        ok = ok and success
        print(('OK   ' if success else 'FAIL ') + label + (': ' + detail if detail else ''))
    report(shutil.which('claude') is not None, 'claude command in PATH')
    report(sys.platform == 'darwin', 'supported OS', 'macOS only; Linux is used for offline tests')
    try:
        c = store.load()
        report(True, 'config syntax and permissions', 'not created yet' if not store.path.exists() else '0600')
    except (SafeError, OSError) as e:
        report(False, 'config syntax and permissions', str(e) if isinstance(e, SafeError) else 'cannot read config')
        return 1
    try:
        store.prepare()
        _probe_writable(store.root)
        report(True, 'management directory writable')
    except (SafeError, OSError):
        report(False, 'management directory', 'cannot create or write management directory')
        return 1
    names = [name] if name else sorted(c['profiles'])
    for n in names:
        p = find_profile(c, n)
        report(True, n + ' required fields')
        try:
            directory = store.ensure_dir(p)
            # Probe write permission without altering Claude-owned settings.
            _probe_writable(directory)
            report(True, n + ' config directory writable')
            anthropic = store.anthropic_dir(p)
            if anthropic is not None:
                _probe_writable(anthropic)
                report(True, n + ' Anthropic config directory writable')
        except (OSError, SafeError) as e:
            report(False, n + ' config directory', str(e) if isinstance(e, SafeError) else 'cannot create or write directory')
            continue
        if p['type'] == 'api':
            # Do not prompt or display the token in doctor.
            token = os.environ.get('CCPROFILE_TOKEN')
            if token is not None:
                try:
                    validate_token(token)
                    report(True, n + ' API token supplied (not saved)')
                except SafeError:
                    report(False, n + ' API token format')
            else:
                print('INFO ' + n + ' API token not supplied; run will prompt on a terminal')
        try:
            paths = settings_paths(directory)
        except (OSError, SafeError) as e:
            report(False, n + ' settings discovery', str(e) if isinstance(e, SafeError)
                   else 'cannot resolve Git worktree settings safely')
            continue
        for path in paths:
            try:
                conflicts = settings_conflicts(path, p['type'] == 'api')
                if conflicts:
                    report(False, n + ' settings conflict', str(path) + ' (' + ', '.join(conflicts) + ')')
            except (OSError, SafeError):
                report(False, n + ' settings check', str(path) + ' (unreadable or invalid JSON)')
        print('INFO ' + n + ' settings checked; API connectivity and model compatibility are untested')
    return 0 if ok else 1


def main(argv=None, store=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    forwarded = []
    if argv and argv[0] == 'run' and '--' in argv:
        split = argv.index('--')
        forwarded, argv = argv[split + 1:], argv[:split]
    args = parser().parse_args(argv)
    store = store or Store()
    if args.command == 'doctor':
        return diagnostic(store, args.name)
    if args.command == 'add':
        def add(c):
            if args.name in c['profiles']:
                raise SafeError('profile already exists; implicit overwrite is not allowed')
            profile = {'id': secrets.token_hex(16), 'type': args.type}
            if args.type == 'claude':
                if any(value is not None for value in (args.base_url, args.model, args.auth, args.context_window)):
                    raise SafeError('claude profiles reject API flags')
                if args.use_default_config:
                    if not (store.home / '.claude').is_dir():
                        raise SafeError('--use-default-config requires an existing ~/.claude directory')
                    profile['use_default_config'] = True
            else:
                if args.use_default_config:
                    raise SafeError('only claude profiles can use ~/.claude')
                profile.update(base_url=args.base_url, model=args.model,
                               auth=args.auth if args.auth is not None else 'bearer')
                if args.context_window is not None:
                    profile['context_window'] = args.context_window
            validate_profile(args.name, profile)
            c['profiles'][args.name] = profile
        store.update(add)
        print('Profile added.')
        return 0
    if args.command == 'edit':
        if all(v is None for v in (args.base_url, args.model, args.auth, args.context_window)) and not args.clear_context_window:
            raise SafeError('provide at least one API setting to edit')
        def edit(c):
            profile = find_profile(c, args.name)
            if profile['type'] != 'api':
                raise SafeError('only API profiles can be edited')
            for key, value in (('base_url', args.base_url), ('model', args.model),
                               ('auth', args.auth), ('context_window', args.context_window)):
                if value is not None:
                    profile[key] = value
            if args.clear_context_window:
                profile.pop('context_window', None)
        store.update(edit)
        print('Profile updated. Supply the token for the selected authentication method on your next run.')
        return 0
    if args.command == 'remove':
        def remove(c):
            return c['profiles'].pop(args.name) if args.name in c['profiles'] else find_profile(c, args.name)
        p = store.update(remove)
        print('Profile definition removed. Claude data retained at ' + str(store.config_dir(p)))
        anthropic = store.anthropic_dir(p)
        if anthropic is not None:
            print('Anthropic profile data retained at ' + str(anthropic))
        return 0
    c = store.load()
    if args.command == 'list':
        rows = [('NAME', 'TYPE', 'MODEL', 'BASE URL', 'TOKEN')]
        for name, p in sorted(c['profiles'].items()):
            rows.append((name, p['type'], p.get('model', '-'), p.get('base_url', '-'),
                         'not saved; supplied at run' if p['type'] == 'api' else '-'))
        widths = [max(len(cell) for cell in column) for column in zip(*rows)]
        for row in rows:
            print('  '.join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip())
        return 0
    p = find_profile(c, args.name)
    if args.command == 'show':
        anthropic = store.anthropic_dir(p)
        print(json.dumps(dict(name=args.name, **p, config_dir=str(store.config_dir(p)),
                              anthropic_config_dir=str(anthropic) if anthropic is not None else None,
                              token_storage='none'), ensure_ascii=False, indent=2))
        return 0
    if args.command == 'login' and p['type'] != 'claude':
        raise SafeError('login is only for claude profiles; API profiles receive a token at run time')
    claude = shutil.which('claude')
    if claude is None:
        raise SafeError('claude command was not found in PATH; install Claude Code first')
    directory = store.ensure_dir(p)
    check_settings(directory, p['type'] == 'api')
    if args.command == 'run':
        if p['type'] == 'api' and any(a == '--model' or a.startswith('--model=') for a in forwarded):
            raise SafeError('--model conflicts with the API profile; use ccprofile edit NAME --model MODEL')
        token = read_token(os.environ) if p['type'] == 'api' else None
        child_args, child_env = build_launch(p, directory, forwarded, os.environ, token)
    else:
        child_args, child_env = build_launch(p, directory, ['auth', 'login'], os.environ)
        try:
            probe = subprocess.run([claude, 'auth', 'login', '--help'], env=child_env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, timeout=10, check=False)
            supported = probe.returncode == 0 and b'auth login' in probe.stdout
        except (OSError, subprocess.TimeoutExpired):
            supported = False
        if not supported:
            raise SafeError('this Claude Code version does not support claude auth login; update Claude Code')
    # No shell evaluation; preserve stdin, tty, exit status, and Unix signals.
    os.execve(claude, [claude, *child_args], child_env)
    return 0


def entrypoint():
    try:
        return main()
    except SafeError as e:
        print('ccprofile: ' + str(e), file=sys.stderr)
        return 1
    except (OSError, UnicodeError):
        # OS errors can include paths or supplied values; suppress raw details.
        print('ccprofile: local operation failed; check file permissions and available disk space', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except PromptInterrupted as e:
        return 128 + e.signum


if __name__ == '__main__':
    sys.exit(entrypoint())
