"""Trusted networkless helper for cold volumes. No extractall or arbitrary archive paths."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tarfile

MAX_BYTES = 8 * 1024**3
MAX_ENTRIES = 100_000


def name_ok(name: str) -> bool:
    return (name == '.' or (bool(name) and not name.startswith('/') and '\\' not in name
            and all(p not in ('', '.', '..') for p in name.split('/'))))


def inventory(archive: Path) -> tuple[list[dict], str]:
    """Validate every entry and content hash before writing any restored file."""
    result, seen, total = [], {}, 0
    with tarfile.open(archive, 'r:gz') as tar:
        for item in tar:
            if (not name_ok(item.name) or item.name in seen or
                    not (item.isdir() or item.isfile()) or item.mode & 0o6000 or
                    (item.mode & 0o1000 and not item.isdir()) or
                    item.uid < 0 or item.gid < 0):
                raise ValueError('unsupported cold archive entry')
            if len(seen) >= MAX_ENTRIES or item.size < 0:
                raise ValueError('cold archive entry limit')
            if item.name == '.' and not item.isdir():
                raise ValueError('root entry must be directory')
            for parent in PurePosixPath(item.name).parents:
                key = str(parent)
                if key in seen and not seen[key]:
                    raise ValueError('archive parent is not directory')
            seen[item.name] = item.isdir()
            total += item.size
            if total > MAX_BYTES:
                raise ValueError('cold archive expanded size limit')
            digest = hashlib.sha256()
            if item.isfile():
                with tar.extractfile(item) as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(chunk)
            result.append({'name': item.name, 'dir': item.isdir(), 'size': item.size,
                           'uid': item.uid, 'gid': item.gid, 'mode': item.mode,
                           'sha256': digest.hexdigest() if item.isfile() else None})
    if not result or '.' not in seen:
        raise ValueError('cold archive root missing')
    # A later file must not turn an earlier child's implicit parent into a file.
    for row in result:
        for parent in PurePosixPath(row['name']).parents:
            if str(parent) in seen and not seen[str(parent)]:
                raise ValueError('conflicting archive parent')
    digest = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return result, digest


def pack(root: Path, destination: Path, volume: str) -> str:
    if destination.exists():
        raise FileExistsError('cold archive exists')
    entries = [root, *sorted(root.rglob('*'))]
    if len(entries) > MAX_ENTRIES:
        raise ValueError('cold volume entry limit')
    with tarfile.open(destination, 'x:gz', dereference=False) as tar:
        for path in entries:
            name = path.relative_to(root).as_posix()
            info = path.lstat()
            # MySQL creates a transient socket link outside the data volume. It is not data.
            if volume == 'mysql_data' and name == 'mysql.sock' and (stat.S_ISLNK(info.st_mode) or stat.S_ISSOCK(info.st_mode)):
                continue
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise ValueError('cold volume contains unsupported link/device/socket')
            item = tar.gettarinfo(str(path), arcname=name)
            # Store hard-linked regular files as independent bytes, never link archive entries.
            if item.islnk():
                item.type, item.linkname, item.size = tarfile.REGTYPE, '', info.st_size
            if item.isfile():
                with path.open('rb') as stream:
                    tar.addfile(item, stream)
            else:
                tar.addfile(item)
    os.chmod(destination, 0o600)
    return inventory(destination)[1]


def unpack(archive: Path, root: Path) -> str:
    expected, digest = inventory(archive)
    if any(root.iterdir()):
        raise ValueError('restore volume is not empty')
    directories = []
    with tarfile.open(archive, 'r:gz') as tar:
        for item in tar:
            path = root / item.name
            path.parent.mkdir(parents=True, exist_ok=True)
            if item.isdir():
                path.mkdir(exist_ok=True)
                directories.append((path, item))
            else:
                with path.open('xb') as out, tar.extractfile(item) as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        out.write(chunk)
                os.chown(path, item.uid, item.gid)
                os.chmod(path, item.mode)
                os.utime(path, (item.mtime, item.mtime))
    for path, item in reversed(directories):
        os.chown(path, item.uid, item.gid)
        os.chmod(path, item.mode)
        os.utime(path, (item.mtime, item.mtime))
    # Independent filesystem readback, including all file bytes and numeric ownership.
    found = {p.relative_to(root).as_posix() for p in [root, *root.rglob('*')]}
    if found != {row['name'] for row in expected}:
        raise ValueError('restored volume inventory mismatch')
    for row in expected:
        path = root / row['name']
        info = path.lstat()
        if (stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid) != (row['mode'], row['uid'], row['gid']):
            raise ValueError('restored volume metadata mismatch')
        if not row['dir']:
            h = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    h.update(chunk)
            if h.hexdigest() != row['sha256']:
                raise ValueError('restored volume content mismatch')
    return digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('pack', 'unpack', 'verify'))
    parser.add_argument('volume')
    args = parser.parse_args()
    path = Path('/transfer') / (args.volume + '.tgz')
    if args.volume not in {'mysql_data', 'agent_data', 'rabbit_data', 'redis_data', 'mongo_data'}:
        raise ValueError('unsupported volume')
    if args.action == 'pack':
        digest = pack(Path('/volume'), path, args.volume)
        # Host CLI is unprivileged; helper must not leave a root-owned temporary file.
        st = Path('/transfer').stat()
        os.chown(path, st.st_uid, st.st_gid)
    elif args.action == 'unpack':
        digest = unpack(path, Path('/volume'))
    else:
        digest = inventory(path)[1]
    print(json.dumps({'verified': True, 'inventory_sha256': digest}))


def run_cli() -> int:
    try:
        main()
        return 0
    except Exception as exc:
        # Fixed structural diagnostics only: no file names, contents, keys or OS error text.
        frames = []
        tb = exc.__traceback__
        while tb:
            if Path(tb.tb_frame.f_code.co_filename).name in ('helper.py', 'backup_volume.py'):
                frames.append({'function': tb.tb_frame.f_code.co_name, 'line': tb.tb_lineno})
            tb = tb.tb_next
        print(json.dumps({'verified': False, 'failure_type': type(exc).__name__, 'helper_frames': frames}))
        return 1


if __name__ == '__main__':
    sys.exit(run_cli())