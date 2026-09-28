"""One-shot, checksum-verified public-source import; no project code is executed.

Only runs on the explicitly authorized repository and development branch. The
workflow-scoped credential is used solely for the final non-forced Git push.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile

SOURCE_COMMIT = 'd2f57756672629492d194df3a5a03331b72bf37e'
SOURCE_TREE = '2954cc354011fcd27a8527f3beb01388a5d98732'
DESTINATION = 'volcano-code/ShopAgentStack'
BRANCH = 'feat/m1.2-integration'
PAYLOAD_SHA256 = '9f7b6bf9025837f70442d633acd227317101ee80ac0ca7977e25bdcb9eac4776'
DECODED_SHA256 = '4a07a0e85a18c26c9b50a6b64086ce0e31d0a33a48de059add64a7945ea4b9a0'
PATCH_SHA256 = '77e992e2ae98786e6d53215bd2ed77907642e144999d5ad5e328f67a4381b5e8'


def git(root: Path, *args: str, data: bytes | None = None) -> bytes:
    result = subprocess.run(['git', '-C', str(root), *args], input=data,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=120)
    if result.returncode:
        # Do not echo transport configuration, credentials or arbitrary data.
        raise RuntimeError('git operation failed: ' + args[0])
    return result.stdout


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_path(value: str) -> Path:
    if (not isinstance(value, str) or not value or '\\' in value or ':' in value
            or PurePosixPath(value).is_absolute()
            or any(c in value for c in '\x00\n\r')
            or any(p in {'', '.', '..', '.git'} for p in value.split('/'))):
        raise ValueError('unsafe source or payload path')
    return Path(*value.split('/'))


def decode_payload(bootstrap: Path) -> dict:
    manifest = json.loads((bootstrap / 'manifest.json').read_text())
    if manifest.get('schema_version') != 1 or len(manifest['parts']) != 9:
        raise ValueError('invalid transport manifest')
    blocks = []
    for i, item in enumerate(manifest['parts']):
        name = f'payload-{i:02d}.xz.part'
        if item['path'] != '.bootstrap/' + name:
            raise ValueError('unexpected payload part')
        part = bootstrap / name
        if part.is_symlink() or not part.is_file():
            raise ValueError('non-regular payload part')
        data = part.read_bytes()
        if len(data) != item['bytes'] or sha(data) != item['sha256']:
            raise ValueError('payload part checksum mismatch')
        blocks.append(data)
    compressed = b''.join(blocks)
    if sha(compressed) != PAYLOAD_SHA256:
        raise ValueError('compressed payload checksum mismatch')
    decoder = lzma.LZMADecompressor(memlimit=128 * 1024 * 1024)
    raw = decoder.decompress(compressed, max_length=2 * 1024 * 1024)
    if not decoder.eof or decoder.unused_data or sha(raw) != DECODED_SHA256:
        raise ValueError('invalid or oversized decoded payload')
    payload = json.loads(raw)
    if payload.get('schema_version') != 1 or payload.get('base_commit') != SOURCE_COMMIT:
        raise ValueError('unsupported source baseline')
    if sha(payload['patch'].encode()) != PATCH_SHA256 or len(payload['targets']) != 65:
        raise ValueError('patch identity mismatch')
    for name, digest in payload['targets'].items():
        safe_path(name)
        if not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ValueError('invalid target digest')
    return payload


def archive_to(source: Path, destination: Path) -> set[str]:
    archive = git(source, 'archive', '--format=tar', SOURCE_COMMIT)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        members = tar.getmembers()
        for item in members:
            safe_path(item.name.rstrip('/'))
            if not (item.isfile() or item.isdir()):
                raise ValueError('source archive contains a link or special file')
        tar.extractall(destination, members=members, filter='data')
        return {item.name for item in members if item.isfile()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    root = Path.cwd().resolve()
    source = args.source.resolve(strict=True)
    if (os.environ.get('GITHUB_REPOSITORY') != DESTINATION
            or os.environ.get('GITHUB_REF') != 'refs/heads/' + BRANCH):
        raise ValueError('unexpected destination repository or branch')
    head = git(root, 'rev-parse', 'HEAD').decode().strip()
    if head != os.environ.get('EXPECTED_HEAD'):
        raise ValueError('checkout differs from triggering commit')
    if git(root, 'status', '--porcelain'):
        raise ValueError('destination worktree must be clean')
    if git(source, 'rev-parse', 'HEAD').decode().strip() != SOURCE_COMMIT:
        raise ValueError('source commit mismatch')
    if git(source, 'rev-parse', 'HEAD^{tree}').decode().strip() != SOURCE_TREE:
        raise ValueError('source tree mismatch')
    author_name = git(root, 'show', '-s', '--format=%an', head).decode().strip()
    author_email = git(root, 'show', '-s', '--format=%ae', head).decode().strip()
    if not author_name or not author_email:
        raise ValueError('no existing authorized commit author metadata')
    payload = decode_payload(root / '.bootstrap')
    with tempfile.TemporaryDirectory(prefix='shop-import-') as tmp:
        prepared = Path(tmp) / 'prepared'
        prepared.mkdir()
        original_paths = archive_to(source, prepared)
        for required in ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'AGENTS.md'):
            if required not in original_paths:
                raise ValueError('required attribution missing')
        git(prepared, 'init', '-q')
        patch = Path(tmp) / 'm12.patch'
        patch.write_text(payload['patch'], encoding='utf-8')
        git(prepared, 'apply', '--check', '--whitespace=error-all', str(patch))
        git(prepared, 'apply', '--whitespace=error-all', str(patch))
        for name, expected in payload['targets'].items():
            if sha((prepared / safe_path(name)).read_bytes()) != expected:
                raise ValueError('post-apply file checksum mismatch: ' + name)
        final_paths = original_paths | set(payload['targets'])
        # GitHub's workflow token must NOT add/change workflow definitions.
        # These files were already published by the authorized connector.
        for name in final_paths:
            if name.startswith('.github/workflows/'):
                if not (root / name).is_file() or (root / name).read_bytes() != (prepared / name).read_bytes():
                    raise ValueError('workflow was not preinstalled unchanged: ' + name)
        for name in final_paths:
            relative = safe_path(name)
            target = root / relative
            if target.is_symlink():
                raise ValueError('destination symlink refused')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(prepared / relative, target)
        for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'AGENTS.md'):
            if git(source, 'show', SOURCE_COMMIT + ':' + name) != (root / name).read_bytes():
                raise ValueError('source attribution unexpectedly changed')
    note = root / 'docs/integration/source-import.md'
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text('# Downstream source import\n\n'
        'Source: `GRIZ200005/shop-agent-stack` at `' + SOURCE_COMMIT + '`.\n\n'
        'Destination: `volcano-code/ShopAgentStack`. Full public snapshot imported, '
        'with original licenses, notices and authorship boundaries preserved. '
        'No source repository Git history, credentials or runtime outputs are imported.\n\n'
        'The M1.2 cumulative patch is SHA256 `' + PATCH_SHA256 + '`. '
        'All 65 patch targets were checked byte-for-byte after applying to the pinned source.\n\n'
        'M1.2 adds optional Agent/MCP tracing, an observation stack, evaluation adapters, '
        'and CI tooling. This source import is not a full-system test pass. '
        'Hosted quality checks, real Collector readback, Java/Web builds, and Docker '
        'image results must be inspected separately. No paid model calls are authorized.\n', encoding='utf-8')
    final_paths.add(note.relative_to(root).as_posix())
    manifest_path = root / 'docs/integration/source-import.json'
    manifest_path.write_text(json.dumps({
        'schema_version': 1, 'source_repository': 'GRIZ200005/shop-agent-stack',
        'source_commit': SOURCE_COMMIT, 'source_tree': SOURCE_TREE,
        'patch_sha256': PATCH_SHA256, 'patch_target_sha256': payload['targets'],
        'upstream_file_count': len(original_paths), 'import_validation': 'byte_verified',
        'full_system_verified': False, 'paid_model_called': False,
    }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    final_paths.add(manifest_path.relative_to(root).as_posix())
    # Keep the authorized bootstrap workflow byte-identical for this push.
    # It has no trigger without READY and is removed later through the connector.
    final_paths.add('.github/workflows/import-m12.yml')
    git(root, 'rm', '-r', '--ignore-unmatch', '.bootstrap')
    git(root, 'add', '-A')
    pathspec = b''.join(name.encode() + b'\0' for name in sorted(final_paths))
    git(root, '--literal-pathspecs', 'add', '-f', '--pathspec-from-file=-',
        '--pathspec-file-nul', data=pathspec)
    actual = set(git(root, 'ls-files', '-z').decode().strip('\0').split('\0'))
    if actual != final_paths:
        raise ValueError('staged file inventory differs from verified snapshot')
    if git(root, 'diff', '--cached', '--name-only', '--', '.github/workflows'):
        raise ValueError('workflow changes cannot be published by this job')
    # Use metadata from the caller's existing authorized commit, not source author.
    git(root, 'config', 'user.name', author_name)
    git(root, 'config', 'user.email', author_email)
    git(root, 'commit', '-m', 'feat: import complete ShopAgentStack snapshot with verified M1.2 integration')
    # No force, no main updates, no secrets or deployments.
    current = git(root, 'ls-remote', 'origin', 'refs/heads/' + BRANCH).decode().split()[0]
    if current != head:
        raise ValueError('remote branch moved; refusing to overwrite concurrent work')
    git(root, 'push', 'origin', 'HEAD:refs/heads/' + BRANCH)
    print(json.dumps({'status': 'pushed_development_branch', 'source_files': len(original_paths),
        'patch_targets': 65, 'files': len(final_paths),
        'commit': git(root, 'rev-parse', 'HEAD').decode().strip(),
        'full_system_verified': False}, sort_keys=True))


if __name__ == '__main__':
    main()
