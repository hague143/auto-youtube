"""Create a source-only ZIP and an integrity-checked clipboard installer."""
import base64
import hashlib
import io
import textwrap
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / 'artifacts'
OUTPUT.mkdir(exist_ok=True)
allowed = ['.env.example', '.gitignore', '.dockerignore', 'Caddyfile', 'Caddyfile.ip', 'compose.yaml', 'compose.ip-https.yaml', 'README.md', 'requirements-dev.txt', 'package.json', 'package-lock.json', 'vercel.json']
for directory in ('server', 'scripts', 'docs', 'web'):
    for path in (ROOT / directory).rglob('*'):
        if path.is_file() and not any(part in ('__pycache__', 'data', '.venv', '.pytest_cache', 'dist', 'node_modules', '.vercel') for part in path.relative_to(ROOT).parts):
            if path.suffix in ('.py', '.txt', '.md', '.sh', '.html', '.js', '.mjs', '.json', '.css', '.woff2') or path.name in ('Dockerfile', '.dockerignore'):
                allowed.append(path.relative_to(ROOT).as_posix())
buffer = io.BytesIO()
with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for name in sorted(set(allowed)):
        info = zipfile.ZipInfo(name)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, (ROOT / name).read_bytes())
payload = buffer.getvalue()
with zipfile.ZipFile(io.BytesIO(payload)) as archive:
    assert archive.testzip() is None
    assert '.env' not in archive.namelist()
    private_env = ROOT / '.env'
    if private_env.exists():
        secrets_to_exclude = [row.split('=', 1)[1] for row in private_env.read_text().splitlines() if row.startswith('STUDIO_ACCESS_TOKEN=')]
        for entry in archive.namelist():
            assert not any(secret and secret.encode() in archive.read(entry) for secret in secrets_to_exclude)
name = 'faceless-studio-oracle-2026-10-28.zip'
(OUTPUT / name).write_bytes(payload)
digest = hashlib.sha256(payload).hexdigest()
(OUTPUT / (name + '.sha256')).write_text(digest + '  ' + name + '\n')
runtime = io.BytesIO()
with zipfile.ZipFile(io.BytesIO(payload)) as source, zipfile.ZipFile(runtime, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as target:
    for name in source.namelist():
        if name in ('compose.yaml', 'compose.ip-https.yaml', 'Caddyfile', 'Caddyfile.ip', '.env.example', '.dockerignore') or (name.startswith('server/') and '/tests/' not in name) or name.startswith('web/src/'):
            target.writestr(name, source.read(name))
runtime_payload = runtime.getvalue()
runtime_digest = hashlib.sha256(runtime_payload).hexdigest()
encoded = '\n'.join(textwrap.wrap(base64.b64encode(runtime_payload).decode(), width=76))
installer = """python3 - <<'STUDIO_INSTALL'
import base64, hashlib, io, os, secrets, zipfile
from pathlib import Path
payload = base64.b64decode('''PAYLOAD'''.encode())
if hashlib.sha256(payload).hexdigest() != 'DIGEST':
    raise SystemExit('Copy was incomplete or changed. No files were written; copy the complete block again.')
destination = Path.home() / 'faceless-studio-app'
if destination.exists():
    raise SystemExit('faceless-studio-app already exists. Nothing was overwritten.')
with zipfile.ZipFile(io.BytesIO(payload)) as archive:
    for name in archive.namelist():
        path = Path(name)
        if path.is_absolute() or '..' in path.parts:
            raise SystemExit('Invalid package path.')
    destination.mkdir(mode=0o700)
    archive.extractall(destination)
fd = os.open(destination / '.env', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
with os.fdopen(fd, 'w') as stream:
    stream.write('SITE_DOMAIN=\\nSTUDIO_ACCESS_TOKEN=' + secrets.token_urlsafe(48) + '\\n')
print('App files extracted into:', destination)
print('Private token created in .env. Do not paste that file into chat.')
print('Set your real domain before starting Docker Compose. No containers were changed.')
STUDIO_INSTALL
""".replace('PAYLOAD', encoded).replace('DIGEST', runtime_digest)
(OUTPUT / 'oracle-copy-paste.txt').write_text(installer)
print('Source ZIP validated:', len(payload), 'bytes;', len(allowed), 'files; no .env/token included.')
print('Clipboard installer prepared:', len(installer), 'characters.')
