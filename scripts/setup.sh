#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/python -m pip install --no-cache-dir -r server/requirements.txt
.venv/bin/python - <<'PY'
from pathlib import Path
import os, secrets
path = Path('.env')
if not path.exists():
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write('STUDIO_ACCESS_TOKEN=' + secrets.token_urlsafe(48) + '\n')
        stream.write('SITE_DOMAIN=\n')
print('Setup complete. Private token is in .env; never paste it into chat.')
PY
