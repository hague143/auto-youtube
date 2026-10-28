import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, readdir, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { buildWeb, backendOrigin } from './build_web.mjs';

test('build includes frontend assets and public config, excluding backend and credentials', async () => {
  const temp = await mkdtemp(path.join(os.tmpdir(), 'faceless-web-test-'));
  try {
    const source = path.resolve('server/static');
    await buildWeb({ source, output: temp, backend: 'https://backend.example/' });
    const names = await readdir(temp);
    assert.deepEqual(names.sort(), ['assets', 'index.html']);
    const config = await readFile(path.join(temp, 'assets/config.js'), 'utf8');
    assert.match(config, /"backendUrl":"https:\/\/backend.example"/);
    assert.match(config, /"frontendOnly":true/);
    assert.ok((await readFile(path.join(temp, 'assets/fonts/manrope-latin.woff2'))).length > 20000);
    assert.match(await readFile(path.join(temp, 'index.html'), 'utf8'), /assets\/config.js/);
    assert.doesNotMatch(config, /STUDIO_ACCESS_TOKEN|API_KEY/);
  } finally { await rm(temp, { recursive: true, force: true }); }
});

test('frontend can build before a backend is configured', async () => {
  const temp = await mkdtemp(path.join(os.tmpdir(), 'faceless-web-test-'));
  try {
    await buildWeb({ source: path.resolve('server/static'), output: temp });
    assert.match(await readFile(path.join(temp, 'assets/config.js'), 'utf8'), /"backendUrl":""/);
  } finally { await rm(temp, { recursive: true, force: true }); }
});

test('public backend config rejects insecure or credential-bearing endpoints', () => {
  for (const value of ['http://backend.example', 'https://user:secret@backend.example', 'https://backend.example/api', 'https://backend.example?token=secret', 'https://backend.example/#secret', 'javascript:alert(1)']) {
    assert.throws(() => backendOrigin(value));
  }
  assert.equal(backendOrigin(' https://backend.example/ '), 'https://backend.example');
});
