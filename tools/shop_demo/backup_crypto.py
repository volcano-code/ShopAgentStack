"""Authenticated, streaming local backup envelope. No keys or plaintext go to stdout."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import stat
import tempfile

MAGIC = b'SHOP-DEMO-BACKUP-1\n'
CHUNK = 1024 * 1024
MAX_ARCHIVE = 8 * 1024**3


def private_file(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError('symlink in recovery path')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('recovery input must be a single regular file')
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError('recovery input must be owned and private (0600)')


def private_parent(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError('symlink in recovery path')
    info = path.parent.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError('create an owned private recovery directory (0700) first')
    if path.exists():
        raise FileExistsError('recovery output already exists')


def keygen(path: Path) -> dict:
    private_parent(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(os.urandom(32))
        stream.flush()
        os.fsync(stream.fileno())
    return {'key_created': True, 'key_file': str(path)}


def read_key(path: Path) -> bytes:
    private_file(path)
    if path.stat().st_size != 32:
        raise ValueError('recovery key must contain exactly 32 random bytes')
    return path.read_bytes()


def encrypt(source: Path, destination: Path, key: bytes) -> None:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    private_parent(destination)
    if len(key) != 32 or not 0 < source.stat().st_size <= MAX_ARCHIVE:
        raise ValueError('invalid key or backup size')
    nonce = os.urandom(12)
    header = MAGIC + nonce
    cipher = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    cipher.authenticate_additional_data(header)
    fd, name = tempfile.mkstemp(prefix='.encrypt-', dir=destination.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, 'wb') as out, source.open('rb') as stream:
            out.write(header)
            for data in iter(lambda: stream.read(CHUNK), b''):
                out.write(cipher.update(data))
            out.write(cipher.finalize())
            out.write(cipher.tag)
            out.flush()
            os.fsync(out.fileno())
        # Atomic no-clobber publication; even a competing writer cannot be overwritten.
        os.link(tmp, destination)
    finally:
        tmp.unlink(missing_ok=True)


@contextmanager
def decrypt(source: Path, key: bytes, directory: Path):
    """Only yield authenticated plaintext AFTER GCM finalize. Always unlink temporary data."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    private_file(source)
    size = source.stat().st_size
    if len(key) != 32 or not len(MAGIC) + 28 < size <= MAX_ARCHIVE + len(MAGIC) + 28:
        raise ValueError('invalid backup envelope size')
    fd, name = tempfile.mkstemp(prefix='.decrypted-', dir=directory)
    plain = Path(name)
    try:
        with os.fdopen(fd, 'wb') as out, source.open('rb') as stream:
            header = stream.read(len(MAGIC) + 12)
            if not header.startswith(MAGIC):
                raise ValueError('unsupported backup envelope')
            stream.seek(-16, os.SEEK_END)
            tag = stream.read(16)
            stream.seek(len(header))
            cipher = Cipher(algorithms.AES(key), modes.GCM(header[-12:], tag)).decryptor()
            cipher.authenticate_additional_data(header)
            remaining = size - len(header) - 16
            while remaining:
                data = stream.read(min(CHUNK, remaining))
                if not data:
                    raise ValueError('truncated backup')
                remaining -= len(data)
                out.write(cipher.update(data))
            try:
                out.write(cipher.finalize())
            except InvalidTag:
                raise ValueError('backup authentication failed') from None
        yield plain
    finally:
        plain.unlink(missing_ok=True)
