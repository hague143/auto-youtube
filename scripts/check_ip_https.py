"""Exercise IP HTTPS without SNI behind Docker, with certificate checks enabled."""
import copy
import json
import socket
import ssl
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGE = 'caddy:2.11.7-alpine'
IP = '203.0.113.10'


def run(*args):
    return subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE).stdout.strip()


def request(port, context, expected_ip=IP):
    # Python omits SNI for IP literals, while still verifying the IP SAN.
    with socket.create_connection(('127.0.0.1', port), timeout=5) as connection:
        with context.wrap_socket(connection, server_hostname=expected_ip) as secure:
            secure.sendall(f'GET /health HTTP/1.1\r\nHost: {IP}\r\nConnection: close\r\n\r\n'.encode())
            chunks = []
            while chunk := secure.recv(4096):
                chunks.append(chunk)
            return b''.join(chunks)


def main():
    containers = []
    with tempfile.TemporaryDirectory(prefix='faceless-ip-https-') as temporary:
        fixture = Path(temporary)
        try:
            run('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                '-keyout', str(fixture / 'ca.key'), '-out', str(fixture / 'ca.pem'),
                '-days', '2', '-subj', '/CN=Faceless IP HTTPS fixture CA',
                '-addext', 'basicConstraints=critical,CA:TRUE',
                '-addext', 'keyUsage=critical,keyCertSign,cRLSign')
            run('openssl', 'req', '-new', '-newkey', 'rsa:2048', '-nodes',
                '-keyout', str(fixture / 'ip.key'), '-out', str(fixture / 'ip.csr'),
                '-subj', '/CN=Faceless IP HTTPS fixture')
            (fixture / 'extensions.cnf').write_text(
                f'subjectAltName=IP:{IP}\nbasicConstraints=critical,CA:FALSE\n'
                'keyUsage=critical,digitalSignature,keyEncipherment\n'
                'extendedKeyUsage=serverAuth\n')
            run('openssl', 'x509', '-req', '-in', str(fixture / 'ip.csr'),
                '-CA', str(fixture / 'ca.pem'), '-CAkey', str(fixture / 'ca.key'),
                '-CAcreateserial', '-out', str(fixture / 'ip.pem'), '-days', '1',
                '-extfile', str(fixture / 'extensions.cnf'))
            (fixture / 'Caddyfile').write_text((ROOT / 'Caddyfile.ip').read_text())
            config = json.loads(run(
                'docker', 'run', '--rm', '-e', f'SITE_DOMAIN={IP}',
                '-v', f'{fixture}:/fixture:ro', IMAGE, 'caddy', 'adapt',
                '--config', '/fixture/Caddyfile', '--adapter', 'caddyfile'))
            issuer = config['apps']['tls']['automation']['policies'][0]['issuers'][0]
            assert issuer['module'] == 'acme' and issuer['profile'] == 'shortlived'
            assert issuer['ca'] == 'https://acme-v02.api.letsencrypt.org/directory'
            server = config['apps']['http']['servers']['srv0']
            assert server['tls_connection_policies'][0]['default_sni'] == IP

            # Load only the fixture certificate into the ordinary TLS cache.
            # No custom certificate selector or public ACME requests are used.
            config['apps']['tls'] = {'certificates': {'load_files': [
                {'certificate': '/fixture/ip.pem', 'key': '/fixture/ip.key'}]}}
            server['automatic_https'] = {'disable': True}
            server['routes'] = [{'handle': [{'handler': 'static_response',
                                           'body': '{"status":"ok"}'}]}]
            original = copy.deepcopy(config)
            for policy in original['apps']['http']['servers']['srv0']['tls_connection_policies']:
                policy.pop('default_sni', None)
            context = ssl.create_default_context(cafile=str(fixture / 'ca.pem'))

            for label, candidate in [('without-default', original), ('with-default', config)]:
                (fixture / f'{label}.json').write_text(json.dumps(candidate))
                name = 'faceless-ip-tls-' + uuid.uuid4().hex[:12]
                run('docker', 'run', '-d', '--name', name,
                    '-p', '127.0.0.1::443', '-v', f'{fixture}:/fixture:ro',
                    IMAGE, 'caddy', 'run', '--config', f'/fixture/{label}.json')
                containers.append(name)
                for attempt in range(100):
                    logs = subprocess.run(['docker', 'logs', name], text=True,
                                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout
                    if 'serving initial configuration' in logs:
                        break
                    time.sleep(0.1)
                else:
                    raise AssertionError(f'Caddy did not become ready: {logs}')
                port = int(run('docker', 'port', name, '443/tcp').splitlines()[0].rsplit(':', 1)[1])
                if label == 'without-default':
                    try:
                        request(port, context)
                    except ssl.SSLError:
                        print('Reproduced: missing SNI fails without the IP default.')
                    else:
                        raise AssertionError('Expected the original missing-SNI handshake to fail.')
                else:
                    response = request(port, context)
                    assert b'200 OK' in response and b'{"status":"ok"}' in response, response
                    try:
                        request(port, context, '203.0.113.11')
                    except ssl.SSLCertVerificationError:
                        pass
                    else:
                        raise AssertionError('TLS must reject a different IP SAN.')
                    print('Passed: no-SNI HTTPS succeeds; certificate trust and IP checks remain enabled.')
        finally:
            for name in containers:
                subprocess.run(['docker', 'rm', '-f', name], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == '__main__':
    main()
