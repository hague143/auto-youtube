# Faceless Studio — private Oracle deployment

A private browser application and Python rendering backend for making narrated videos with your own provider keys. The current package is a browser/backend edition; Android release binaries and public multi-user accounts are not included.

## Included

Single-owner authenticated FastAPI server; Gemini script or manual narration; natural Edge TTS with word timings and an explicit offline eSpeak option; scene-specific Pexels videos/photos and Pixabay photos; MoviePy/FFmpeg MP4 assembly timed to narration; speech-timed captions (estimated within offline scenes); optional separately installed Whisper; title covers and optional Hugging Face inference; private project list, preview, downloads and deletion. English voices only. Direct social auto-upload, Android packaging, user accounts and billing are not included in this rebuild.

For natural narration, select Aria, Guy or Sonia in Create. For footage, select Pexels / Pixabay stock media and add a stock key in Settings; a Gemini key also enables concrete visual search planning for each scene. Long source clips are trimmed to their narration scene. A script that exceeds the selected length produces an actionable error instead of cutting off its ending. See [video quality and timing](docs/VIDEO_QUALITY.md).

API keys stay in browser memory and are sent to your own backend for the requested operation. Keys are not stored with project metadata. Refreshing the page clears browser credentials. Server outputs persist in the Docker data volume. The server processes one render at a time and holds at most 50 projects. Provider quotas and availability apply; free hosting and inference are not guaranteed.

The browser interface includes a studio sidebar, CSS 3D poster stack and pointer tilt, a live illustrative layout preview, sample narration, authenticated project covers and clearer API-key setup. The preview illustrates format and text placement; it is not a finished rendered video. Decorative animation respects reduced-motion preferences, and pointer tilt is limited to suitable desktop pointers. Fonts and styles are served locally without a CDN.

## Vercel frontend

Set the Vercel **Root Directory to `web`**, Framework Other, install command `npm ci --ignore-scripts`, build command `npm run build`, and output directory `dist`. The `web` project contains only frontend files and its own Node lockfile/config; Python stays outside that project. Do not include source files outside the Root Directory in the Vercel build. The frontend gets an actual `.vercel.app` address from Vercel. Set the optional public `STUDIO_BACKEND_URL` to the real Oracle HTTPS origin, or enter it at runtime in Settings. Never put access tokens or provider keys in the public build. Video rendering and persistent files stay on Oracle. See [Vercel instructions](docs/VERCEL.md), including the no-DuckDNS IP-certificate option.

For local frontend checks, run `npm ci --ignore-scripts` inside `web`, then `npm run test:build` and `npm run build`. Root-level npm scripts delegate to the same project and generate `web/dist`.

Manrope variable font is distributed under the SIL Open Font License; see `web/src/fonts/OFL.txt`. Source: `@fontsource-variable/manrope` 5.2.8, Latin variable WOFF2.

## Oracle deployment

Requires Docker + Compose on Ubuntu, inbound TCP 80/443 on the attached Oracle security list, a hostname resolving to the VM public IP, and the package files on the VM. ARM images are available upstream; the deployment must still be validated on the user's actual ARM VM.

1. Put these files in a private application directory on your VM. Source transfer has not been performed by this cloud session.
2. Copy `.env.example` to `.env`, use your real hostname for `SITE_DOMAIN`, and generate a private random token (32+ characters) for `STUDIO_ACCESS_TOKEN`. `chmod 600 .env`. Never embed the token in app source or publish it to GitHub.
3. Stop only the temporary nginx test container, if still running: `sudo docker rm -f faceless-http-check`.
4. From this directory: `sudo docker compose up --build -d`.
5. Check local health: `curl --fail http://127.0.0.1:8000/health`; then HTTPS health with your real hostname. Check `sudo docker compose logs --tail 40 web studio` if startup/certificates fail. Keep `.env` out of logs/screenshots.
6. Open your HTTPS hostname. Settings already shows the same server origin. Enter your private server token; test connection and apply. For a no-provider-key test, paste a short English narration, select Local English, Studio backgrounds and script captions, then create and download an MP4.

Caddy persists certificates in its own volumes and redirects HTTP to HTTPS. Port 8000 binds only to loopback. Do not run `docker compose down -v` unless you intend to delete projects/certificates. Back up the `studio-data` volume privately. Changing the public IP requires updating DNS. DuckDNS is an independent free subdomain service; keep its account/update token private. A successful nginx page only verifies networking, not application deployment.

The access token gives its holder access to all projects. This architecture is for private testing. Public Play Store use requires per-user authentication, data isolation, resource quotas, abuse controls, privacy disclosures, signed Android release builds and real-device testing.

## Cloud development

`bash scripts/setup.sh`, then `.venv/bin/python -m uvicorn main:app --app-dir server --host 127.0.0.1 --port 8000 --workers 1 --no-access-log` from the repository root. Install FFmpeg, DejaVu fonts and eSpeak with the system package manager, or use the Docker image. Internal health checks are supported; this cloud environment does not provide a public browser preview.

Validation results are recorded in `docs/VALIDATION.md`. Live provider requests and deployment on Oracle are separate checks.

For UI fixture checks, start a separate temporary development process from the root with `STUDIO_DATA_DIR=/tmp/faceless-ui-data .venv/bin/python -m uvicorn main:app --app-dir server --host 127.0.0.1 --port 18002 --workers 1 --no-access-log`, then run `.venv/bin/python scripts/check_ui.py`. This checks local assets, responsive layouts, preview controls, credential behavior, desktop tilt and reduced-motion/touch handling without provider calls. Stop only that temporary process afterward. The real container/render smoke test remains `scripts/check_container.py`.
