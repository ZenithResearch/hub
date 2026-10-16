"""Finite host Docker-client lifecycle; no image or authority policy changes."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import time
import uuid


MAX_OUTPUT = 8 * 1024**2
MAX_TOTAL_OUTPUT = 64 * 1024**2
MAX_BYTES = 2 * 1024**3
MAX_ENTRIES = 12000
MIN_FREE = 4 * 1024**3
TOTAL_SECONDS = 7200
LABEL = 'io.hub.image-execution'


class ExecutionFailure(subprocess.SubprocessError):
    pass


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def save(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.next')
    with temporary.open('x') as output:
        json.dump(value, output, sort_keys=True, indent=2, allow_nan=False)
        output.write('\n')
    os.replace(temporary, path)


def group_exists(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


class ImageExecution:
    def __init__(self, output, docker, checkpoints):
        self.output, self.docker = Path(output), docker
        self.directory = self.output / 'execution'
        self.directory.mkdir(mode=0o700)
        self.scratch = self.directory / 'scratch'
        self.scratch.mkdir(mode=0o700)
        self.token = uuid.uuid4().hex
        self.started = time.monotonic()
        self.first_failure, self.secondary_errors = None, []
        self.commands, self.containers, self.builds = [], [], []
        self.paths = [Path(p) for p in checkpoints]
        self.before = self.snapshot()
        self.finished = False
        self.old_handlers = {}
        self.persist()

    def snapshot(self):
        result = {}
        for path in self.paths:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise ExecutionFailure('Nonregular input checkpoint')
            result[str(path)] = {'bytes': info.st_size, 'sha256': digest(path)}
        return result

    def fail(self, phase, error, *, secondary=False):
        row = {'phase': phase, 'error_type': type(error).__name__, 'detail': str(error)}
        if self.first_failure is None and not secondary:
            self.first_failure = row
        else:
            self.secondary_errors.append(row)

    def persist(self):
        save(self.directory / 'lifecycle.json', {
            'schema': 'hub.component-image-execution.v1', 'token': self.token,
            'first_failure': self.first_failure, 'secondary_errors': self.secondary_errors,
            'commands': self.commands, 'containers': self.containers, 'builds': self.builds,
            'inputs_before': self.before, 'finished': self.finished,
            'bounds': {'command_output_bytes': MAX_OUTPUT, 'total_output_bytes': MAX_TOTAL_OUTPUT,
                       'owned_bytes': MAX_BYTES, 'owned_entries': MAX_ENTRIES,
                       'minimum_free_bytes': MIN_FREE, 'execution_seconds': TOTAL_SECONDS},
        })

    def install_handlers(self):
        def interrupted(number, frame):
            raise ExecutionFailure('Host execution interrupted by signal ' + str(number))
        for number in (signal.SIGINT, signal.SIGTERM):
            self.old_handlers[number] = signal.signal(number, interrupted)

    def restore_handlers(self):
        for number, handler in self.old_handlers.items():
            signal.signal(number, handler)
        self.old_handlers.clear()

    def limits(self):
        entries = size = total_output = 0
        for base, directories, files in os.walk(self.output, followlinks=False):
            for name in directories + files:
                path = Path(base) / name
                info = path.lstat()
                entries += 1
                if stat.S_ISREG(info.st_mode):
                    size += info.st_size
                    if name in ('stdout', 'stderr'):
                        total_output += info.st_size
                elif not stat.S_ISDIR(info.st_mode):
                    raise ExecutionFailure('Nonregular owned output')
        if entries > MAX_ENTRIES or size > MAX_BYTES or total_output > MAX_TOTAL_OUTPUT:
            raise ExecutionFailure('Owned output budget exceeded')
        if shutil.disk_usage(self.output).free < MIN_FREE:
            raise ExecutionFailure('Owned output free-space reserve exhausted')
        if time.monotonic() - self.started >= TOTAL_SECONDS:
            raise ExecutionFailure('Overall image execution deadline exceeded')

    def _reap(self, child, row, cancel_signal):
        # Direct child is reaped before each census so its zombie is not mistaken
        # for a surviving group. No signal is ever sent to an inferred PGID.
        for number, seconds in ((cancel_signal, 3), (signal.SIGKILL, 2)):
            child.poll()
            if not group_exists(child.pid):
                break
            os.killpg(child.pid, number)
            row['signals'].append(int(number))
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                child.poll()
                if not group_exists(child.pid):
                    break
                time.sleep(.05)
        child.wait(timeout=2)
        for _ in range(51):
            if not group_exists(child.pid):
                row['group_absent'] = True
                return
            time.sleep(.1)
        raise ExecutionFailure('Owned client process group remains present')

    def run(self, label, argv, *, timeout=20, accepted=(0,), cleanup=False,
            cancel_signal=signal.SIGTERM):
        if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,99}', label):
            raise ExecutionFailure('Invalid command label')
        folder = self.directory / label
        folder.mkdir(mode=0o700)
        row = {'label': label, 'argv': list(argv), 'cwd': str(self.output),
               'timeout_seconds': timeout, 'state': 'not_started', 'signals': [],
               'group_absent': False, 'returncode': None}
        self.commands.append(row)
        self.persist()
        child, problem = None, None
        started = time.monotonic()
        try:
            if not cleanup:
                self.limits()
            with (folder / 'stdout').open('xb') as out, (folder / 'stderr').open('xb') as err:
                environment = dict(os.environ, TMPDIR=str(self.scratch))
                child = subprocess.Popen(argv, cwd=self.output, env=environment,
                                         stdout=out, stderr=err, start_new_session=True)
                row.update(state='running', owned_pgid=child.pid)
                self.persist()
                while True:
                    if out.tell() + err.tell() > MAX_OUTPUT:
                        raise ExecutionFailure('Command output budget exceeded')
                    if not cleanup:
                        self.limits()
                    if time.monotonic() - started >= timeout:
                        raise ExecutionFailure('Command deadline exceeded')
                    if child.poll() is not None:
                        break
                    time.sleep(.05)
                row['returncode'] = child.returncode
                if child.returncode not in accepted:
                    raise ExecutionFailure('Command returned ' + str(child.returncode))
        except BaseException as error:
            problem = error
            self.fail(label, error, secondary=cleanup)
        finally:
            if child is not None:
                try:
                    self._reap(child, row, cancel_signal)
                    row['returncode'] = child.returncode
                except BaseException as error:
                    self.fail(label + '.client_cleanup', error, secondary=problem is not None or cleanup)
                    problem = problem or error
            row['seconds'] = time.monotonic() - started
            row['state'] = 'failed' if problem else 'passed'
            for stream in ('stdout', 'stderr'):
                path = folder / stream
                if path.exists():
                    row[stream + '_bytes'] = path.stat().st_size
                    row[stream + '_sha256'] = digest(path)
            if row.get('stdout_bytes', 0) + row.get('stderr_bytes', 0) > MAX_OUTPUT:
                error = ExecutionFailure('Command output budget exceeded')
                if problem is None:
                    problem = error
                    row['state'] = 'failed'
                    self.fail(label, error, secondary=cleanup)
            self.persist()
        if problem is not None:
            raise problem
        return subprocess.CompletedProcess(argv, row['returncode'],
            (folder / 'stdout').read_bytes(), (folder / 'stderr').read_bytes())

    def _inspect(self, label, identity):
        result = self.run(label, [self.docker, 'container', 'inspect', identity],
                          timeout=5, accepted=(0, 1), cleanup=True)
        if result.returncode:
            suffixes = ('No such container: ' + identity, 'No such object: ' + identity)
            if result.stdout.strip() in (b'', b'[]') and any(
                    result.stderr.decode(errors='replace').strip().endswith(s) for s in suffixes):
                return None
            raise ExecutionFailure('Container absence is unproved')
        value = json.loads(result.stdout)
        if type(value) is not list or len(value) != 1 or type(value[0]) is not dict:
            raise ExecutionFailure('Ambiguous container inspection')
        return value[0]

    def cleanup_container(self, row):
        if not row['attempted'] or row['absent']:
            return
        identity = row['name']
        cidfile = Path(row['cidfile'])
        if cidfile.exists() or cidfile.is_symlink():
            info = cidfile.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > 65:
                raise ExecutionFailure('Unsafe owned cidfile')
            identity = cidfile.read_text().strip()
            if not re.fullmatch('[a-f0-9]{64}', identity):
                raise ExecutionFailure('Malformed owned cidfile')
        for step in range(3):
            value = self._inspect(row['phase'] + '.inspect-' + str(step), identity)
            if value is None:
                # A cid may disappear while a colliding name remains; verify both.
                if identity != row['name'] and self._inspect(row['phase'] + '.name-absent', row['name']) is not None:
                    raise ExecutionFailure('Owned name is not absent')
                row['absent'] = True
                return
            config = value.get('Config', {})
            if (value.get('Name') != '/' + row['name']
                    or config.get('Labels', {}).get(LABEL) != self.token
                    or config.get('Image') != row['image']
                    or not re.fullmatch('[a-f0-9]{64}', value.get('Id', ''))
                    or (identity != row['name'] and value['Id'] != identity)):
                raise ExecutionFailure('Refuse cleanup of mismatching container identity')
            identity = value['Id']
            row['container_id'] = identity
            if step == 0:
                self.run(row['phase'] + '.stop', [self.docker, 'container', 'stop', '--timeout', '3', identity],
                         timeout=5, accepted=(0, 1), cleanup=True)
            elif step == 1:
                self.run(row['phase'] + '.remove', [self.docker, 'container', 'rm', '--force', identity],
                         timeout=5, accepted=(0, 1), cleanup=True)
        raise ExecutionFailure('Owned container remains present')

    def container(self, label, image, arguments):
        name = 'hub-image-' + self.token + '-' + label
        cidfile = self.directory / (label + '.cid')
        row = {'phase': label, 'name': name, 'image': image, 'cidfile': str(cidfile),
               'attempted': False, 'absent': False}
        # Existing names are never adopted or removed, even if labels match.
        if self._inspect(label + '.preflight', name) is not None:
            raise ExecutionFailure('Container name collision')
        row['attempted'] = True
        self.containers.append(row)
        self.persist()
        problem = None
        try:
            return self.run(label, [self.docker, 'run', '--rm', '--name', name,
                '--cidfile', str(cidfile), '--label', LABEL + '=' + self.token,
                '--pull=never', '--network=none', '--read-only', '--cap-drop=ALL',
                '--security-opt=no-new-privileges', '--tmpfs', '/data:uid=10001,gid=10001,mode=0700',
                '--tmpfs', '/tmp:mode=1777', '--entrypoint', '/opt/component/bin/python',
                image, '-I', '-B', *arguments], timeout=90)
        except BaseException as error:
            problem = error
            raise
        finally:
            try:
                self.cleanup_container(row)
            except BaseException as error:
                self.fail(label + '.daemon_cleanup', error, secondary=problem is not None)
                if problem is None:
                    raise
            finally:
                self.persist()

    @staticmethod
    def history_rows(raw):
        value = json.loads(raw)
        if type(value) is not list or any(type(row) is not dict for row in value):
            raise ExecutionFailure('Unsupported build history JSON')
        return value

    def build(self, label, argv):
        name = 'hub-image-' + self.token + '-' + label
        metadata = self.directory / (label + '.metadata.json')
        row = {'phase': label, 'name': name, 'terminal_observed': False, 'ref': None}
        # Probe support before launching daemon work. No unsupported cancellation
        # command, history deletion, builder replacement or global cleanup exists.
        listing = self.run(label + '.history-before', [self.docker, 'buildx', '--builder', 'default',
                           'history', 'ls', '--format', 'json'], timeout=10)
        if any(item.get('Name') == name for item in self.history_rows(listing.stdout)):
            raise ExecutionFailure('Build name collision')
        self.builds.append(row)
        self.persist()
        command = list(argv[:-1]) + ['--metadata-file', str(metadata), '--build-arg',
                                   'BUILDKIT_BUILD_NAME=' + name, argv[-1]]
        problem = None
        try:
            return self.run(label, command, timeout=900, cancel_signal=signal.SIGINT)
        except BaseException as error:
            problem = error
            raise
        finally:
            try:
                if metadata.exists():
                    if metadata.is_symlink() or metadata.stat().st_size > MAX_OUTPUT:
                        raise ExecutionFailure('Unsafe build metadata')
                    ref = json.loads(metadata.read_bytes()).get('buildx.build.ref')
                else:
                    ref = None
                for attempt in range(3):
                    if ref is None:
                        listing = self.run(label + '.history-' + str(attempt), [self.docker, 'buildx',
                            '--builder', 'default', 'history', 'ls', '--format', 'json'], timeout=10, cleanup=True)
                        matches = [item for item in self.history_rows(listing.stdout) if item.get('Name') == name]
                        if len(matches) > 1:
                            raise ExecutionFailure('Ambiguous owned build record')
                        if matches:
                            ref = matches[0].get('ID')
                    if type(ref) is str and re.fullmatch(r'[A-Za-z0-9_./-]{1,512}', ref):
                        result = self.run(label + '.history-inspect-' + str(attempt), [self.docker,
                            'buildx', '--builder', 'default', 'history', 'inspect', '--format', 'json', ref],
                            timeout=10, cleanup=True)
                        value = json.loads(result.stdout)
                        completed = value.get('CompletedAt')
                        if (value.get('Name') == name and value.get('Ref') == ref.rsplit('/', 1)[-1]
                                and value.get('Status') in ('completed', 'failed', 'error', 'canceled', 'cancelled')
                                and type(completed) is str and not completed.startswith('0001-')):
                            datetime.datetime.fromisoformat(completed.replace('Z', '+00:00'))
                            row.update(ref=ref, terminal_observed=True, status=value['Status'])
                            if problem is None and value['Status'] != 'completed':
                                raise ExecutionFailure('Build did not complete successfully')
                            break
                    time.sleep(.5)
                if not row['terminal_observed']:
                    raise ExecutionFailure('Daemon build completion/cancellation is unproved')
            except BaseException as error:
                self.fail(label + '.daemon_completion', error, secondary=problem is not None)
                if problem is None:
                    raise
            finally:
                self.persist()

    def finish(self):
        if not self.finished:
            for row in self.containers:
                if not row['absent']:
                    self.fail(row['phase'] + '.daemon_cleanup', ExecutionFailure('Container cleanup unproved'))
            for row in self.builds:
                if not row['terminal_observed']:
                    self.fail(row['phase'] + '.daemon_completion', ExecutionFailure('Build terminal state unproved'))
            for row in self.commands:
                if row.get('owned_pgid') and group_exists(row['owned_pgid']):
                    self.fail(row['label'] + '.final_group', ExecutionFailure('Owned client group remains present'))
            try:
                after = self.snapshot()
                save(self.directory / 'inputs-after.json', after)
                if after != self.before:
                    raise ExecutionFailure('Input checkpoint drift')
                if self.first_failure is None:
                    self.limits()
            except BaseException as error:
                self.fail('final_checkpoints', error)
            self.finished = True
        self.persist()
        files = []
        for base, directories, names in os.walk(self.output, followlinks=False):
            for name in directories + names:
                path = Path(base) / name
                info = path.lstat()
                relative = str(path.relative_to(self.output))
                if relative in ('execution/payloads.json', 'image-manifest.json'):
                    continue
                if stat.S_ISREG(info.st_mode):
                    files.append({'path': relative, 'bytes': info.st_size,
                                  'sha256': digest(path), 'mode': stat.S_IMODE(info.st_mode)})
                elif not stat.S_ISDIR(info.st_mode):
                    raise ExecutionFailure('Unsafe final owned output')
        save(self.directory / 'payloads.json', {'schema': 'hub.component-image-execution-payloads.v1',
            'files': sorted(files, key=lambda row: row['path']),
            'exclusions': ['execution/payloads.json', 'image-manifest.json'],
            'note': 'The unchanged success artifact is published only after this lifecycle gate.'})
        if self.first_failure is not None or self.secondary_errors:
            raise ExecutionFailure('Image execution incomplete; inspect execution/lifecycle.json')
