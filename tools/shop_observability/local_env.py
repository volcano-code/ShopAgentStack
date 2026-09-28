"""Create a private local Grafana password file. Never print its value or overwrite."""
import argparse
import os
from pathlib import Path
import secrets


def create(path: Path) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write('SHOP_OBS_GRAFANA_PASSWORD=' + secrets.token_urlsafe(32) + '\n')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--path', type=Path, default=Path('deploy/observability/.env'))
    args = parser.parse_args()
    try:
        create(args.path)
    except OSError:
        print('Not written: parent must exist and the private env file must be new.')
        return 2
    print('Private local env created; password not printed. Do not commit this file.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
