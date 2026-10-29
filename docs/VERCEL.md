# Vercel frontend + Oracle rendering backend

Vercel hosts the static browser interface. Oracle runs FFmpeg/MoviePy, voice generation, persistent output files and the in-process render queue. This backend is not a Vercel Function: its work continues outside individual HTTP requests and needs persistent storage.

## Frontend deployment

After this repository is successfully published to GitHub, import it using Vercel **Add New → Project → Import Git Repository**.

- Root directory: **`web`**. Disable inclusion of source files outside the Root Directory.
- Framework: Other (`framework: null` in vercel.json).
- Install command: `npm ci --ignore-scripts`.
- Build command: `npm run build`.
- Output directory: `dist`.
- Node version: 24.x.
- No provider API keys or server token in Vercel environment variables.
- Optional public variable: `STUDIO_BACKEND_URL`, containing only the trusted HTTPS origin of your actual rendering backend. Leave unset to enter the URL in Settings at runtime.

The isolated web project has its own package.json, lockfile, build script and vercel.json. The build copies only frontend assets and a public connection config into dist; Python code, .env and video outputs are outside this project. Vercel supplies the actual `.vercel.app` hostname after deployment. No deployment or hostname has been created by this workspace.

If a prior deployment used the repository root and failed while installing pydantic-core on Python 3.14, change Settings → Build and Deployment → Root Directory to `web`, save, and redeploy the latest main commit containing this directory. The earlier root deployment tried to install Oracle-only Python dependencies. The solution is project isolation; no Python ABI compatibility override or backend dependency upgrade is needed for the static frontend.

The UI opens before a backend is connected. Generation requires the backend. Settings allows a trusted HTTPS server origin and private access token. Credentials and a runtime-entered URL remain only in this page session and clear on refresh; a public build-time backend origin can be restored automatically on page load. The self-hosted Docker version defaults to its own origin as before.

## Backend connection

The Oracle backend must present a browser-trusted HTTPS certificate. A Vercel frontend does not add HTTPS to a plain HTTP Oracle endpoint. Do not use an HTTP reverse proxy between public clouds for bearer tokens/provider keys.

Set `STUDIO_CORS_ORIGINS` on the backend to include the exact Vercel frontend origin, preserving other legitimate origins. Example only:

```dotenv
STUDIO_CORS_ORIGINS=https://YOUR_ACTUAL_PROJECT.vercel.app,https://localhost,http://localhost,capacitor://localhost
```

Use your actual Vercel hostname, not this placeholder. Restart the backend after environment changes. Vercel preview deployments have different hostnames; allow only the specific preview origin you need rather than every `*.vercel.app` site.

In Settings, enter the actual Oracle backend HTTPS origin, its private server access token, and whichever provider keys you want to use. Run Test connection and Apply settings. For a first render, use manual English narration, Local English and Studio backgrounds. Confirm creation, status polling, authenticated cover/MP4 downloads and deletion from the real Vercel site.

## Oracle HTTPS without DuckDNS

Let’s Encrypt made six-day and public IP-address certificates generally available on January 15, 2026. Its IP certificates require the `shortlived` ACME profile. Caddy supports that profile and automated renewal. Official sources:

- https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability
- https://caddyserver.com/docs/caddyfile/directives/tls#acme

The optional `Caddyfile.ip` and `compose.ip-https.yaml` configure a pinned Caddy 2.11.7 image with the explicit Let's Encrypt issuer and shortlived profile, replacing the ordinary hostname Caddyfile. The global `default_sni` selects the public IP certificate for browsers that omit SNI when opening an IP address. This is necessary behind Docker's port mapping, where Caddy's listener address is a private container IP. On the actual Oracle VM, once the application files have been transferred:

1. Keep public TCP 80/443 ingress enabled. Set root `.env` `SITE_DOMAIN` to the VM's actual public IPv4, without a scheme or port, and set `STUDIO_CORS_ORIGINS` to include the actual Vercel frontend origin. Preserve the existing private server token.
2. Stop only the temporary test container when ready to replace it: `sudo docker rm -f faceless-http-check`.
3. From the app directory: `sudo docker compose -f compose.yaml -f compose.ip-https.yaml up --build -d`.
4. Inspect Caddy logs: `sudo docker compose -f compose.yaml -f compose.ip-https.yaml logs --tail 50 web`. Verify an ordinary HTTPS request to the actual public IP and `/health`, with certificate verification enabled. Do not use `curl -k` or browser certificate exceptions.
5. Only after certificate issuance succeeds, use that HTTPS IP origin as `STUDIO_BACKEND_URL` in Vercel or enter it in Settings. Test authentication, CORS and one real render from the real Vercel site.

For an existing deployment, run `git pull --ff-only` and then `sudo docker compose -f compose.yaml -f compose.ip-https.yaml up -d --no-deps --force-recreate web`. Recreating the proxy also refreshes the file bind mount after Git replaces `Caddyfile.ip`. This preserves the running backend, projects and certificate volumes. A `certificate obtained successfully` log confirms issuance; also verify an ordinary HTTPS request to the IP, because issuance alone does not test certificate selection for clients without SNI.

No public certificate has been requested or issued by this cloud session. Config validation is separate from successful issuance on Oracle. The VM must remain reachable for automatic renewal; IP certificates are short-lived, about six days. Caddy's certificate data/config volumes must persist. If the public IP changes, update SITE_DOMAIN and the frontend backend URL, and obtain/validate the certificate for the new IP. A reserved public IP is useful when keeping a fixed backend address.

DuckDNS is not required for this configuration. A regular hostname remains another supported option. Do not assume that typing `https://` before an IP makes it trusted, or accept a self-signed certificate as a public deployment.

## Checks and limits

`python3 scripts/check_ip_https.py` checks IP certificate selection using the pinned Caddy image and an isolated local test CA. It reproduces the missing-SNI failure without the default, verifies a successful TLS handshake with the default, and rejects a certificate for a different IP. It does not contact an ACME server or issue a public certificate.

`npm run test:build` exercises frontend-only packaging and public config validation. `npm run build` prepares a static export. Browser checks for the static export must verify that API requests go to the configured backend, not `/api` on Vercel. Actual Vercel deployment, Oracle HTTPS, real CORS and serverless-provider plan quotas remain separate checks. The application still has a single private owner; this change does not add public user accounts or Play Store release readiness.
