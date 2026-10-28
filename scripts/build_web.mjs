import { cp, mkdir, readdir, rm, writeFile } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

export function backendOrigin(value = '') {
  if (!value.trim()) return '';
  let url;
  try { url = new URL(value.trim()); } catch { throw new Error('STUDIO_BACKEND_URL must be a valid HTTPS origin.'); }
  if (url.protocol !== 'https:' || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('STUDIO_BACKEND_URL must use HTTPS without credentials, paths, query or fragment.');
  }
  return url.origin;
}

export async function buildWeb({ source, output, backend = '' }) {
  const backendUrl = backendOrigin(backend);
  if (path.resolve(source) === path.resolve(output)) throw new Error('Source and output must be separate directories.');
  await rm(output, { recursive: true, force: true });
  const assets = path.join(output, 'assets');
  await mkdir(assets, { recursive: true });
  await cp(path.join(source, 'index.html'), path.join(output, 'index.html'));
  for (const entry of await readdir(source)) {
    if (entry !== 'index.html' && entry !== 'config.js') {
      await cp(path.join(source, entry), path.join(assets, entry), { recursive: true });
    }
  }
  const config = JSON.stringify({ backendUrl, frontendOnly: true }).replaceAll('<', '\\u003c');
  await writeFile(path.join(assets, 'config.js'), `window.STUDIO_CONFIG = Object.freeze(${config});\n`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const root = fileURLToPath(new URL('../', import.meta.url));
  await buildWeb({ source: path.join(root, 'server/static'), output: path.join(root, 'web-dist'), backend: process.env.STUDIO_BACKEND_URL || '' });
  console.log('Static frontend built in web-dist. Rendering backend and credentials are excluded.');
}
