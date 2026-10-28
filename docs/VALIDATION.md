# Validation — 2026-10-28 rebuilt private browser edition

- `python -m pytest -q server/tests`: **5 passed**, including authentication, input/key redaction, request size limit, provider requirements, artifact allowlist, restart recovery, deletion and a real H.264/AAC FFmpeg render with captions. One upstream Starlette/AnyIO deprecation warning.
- Docker image built successfully on the cloud's **x86_64** Docker daemon. An initial read-permission failure was fixed by assigning copied app files to the non-root runtime user.
- The cloud build proxy CA was supplied using an optional BuildKit secret containing the operating system CA bundle. TLS verification stayed enabled. Oracle does not need that build secret unless its network also has a separately configured enterprise CA.
- `python scripts/check_container.py`: **passed**, using its own temporary container and system Chromium. Real eSpeak narration, studio background visuals, MoviePy/FFmpeg video assembly, script captions, authenticated preview/download, credential clearing on page refresh, project persistence across refresh, desktop and mobile layout, and deletion were exercised. No provider API keys were used.
- Actual exported MP4 duration **6.370 seconds**, H.264 video + AAC audio. Sample output and screenshots are generated under ignored `artifacts/`. This is a local test export, not a hosted public download.
- `SITE_DOMAIN=validation.invalid docker compose config -q`: passed. The hostname is a validation fixture, not a deployed site.
- Official Caddy image validated the Caddyfile successfully with a fixture hostname. This checks config syntax/provisioning, not public certificate issuance.
- Source ZIP upload was attempted once through the existing GitHub credential binding after this environment restoration. GitHub returned **403 Resource not accessible by integration**. No source file was published and no download URL was created. Do not ask the user to regenerate their token again; use the prepared clipboard transfer if a supported attachment is unavailable.
- Live Gemini, stock-provider, Edge TTS, Hugging Face inference and optional Whisper were **not validated** in this rebuild. Their availability/quotas are separate from local pipeline checks.
- Deployment on the user's Oracle **ARM** VM, real DNS and trusted public HTTPS are **not yet validated**. The user verified only the temporary public nginx page.
- Android APK/AAB files from the earlier prototype are not present in this restored checkout. This rebuilt package is a private browser/backend edition; no Play Store release has been built or submitted.

Do not describe local test results as successful Oracle deployment or production multi-user readiness.

## UI redesign checks

The browser UI was redesigned with a studio sidebar, local Manrope font, CSS 3D poster artwork, pointer tilt, live illustrative format/text preview, sample narration, project covers and grouped API-key settings. API and rendering behavior remain compatible.

`scripts/check_ui.py` verifies responsive layouts at 1440, 1024, 768, 600, 390 and 320 CSS pixels, live topic/format/caption changes, literal handling of HTML-like user text, sample-story controls, navigation, masked credentials, connection feedback, and credential clearing on refresh. The checks use isolated API fixtures and request no external font/CDN assets. Desktop pointer, reduced-motion and touch-device handling have separate checks.

After the redesign, all 5 backend tests and the real Docker/browser rendering smoke test passed again. The actual local eSpeak export remained 6.370 seconds with H.264/AAC, authenticated preview/download and deletion. Screenshots are generated under ignored `artifacts/`. The rebuilt container still needs deployment and HTTPS validation on the user's actual Oracle VM.

## Vercel export and no-DuckDNS configuration

- `npm run test:build`: **3 passed**, covering frontend-only output, optional backend config, and rejection of insecure/credential-bearing public endpoints.
- `npm run build`: passed. Only static frontend assets and a public connection config are emitted; backend, .env and generated videos are excluded.
- `scripts/check_vercel.py`: passed with browser API fixtures. The actual built static frontend made authenticated create/status/download requests to a separate HTTPS backend origin, rejected a public HTTP backend, cleared credentials/runtime URL on refresh, and made no `/api` requests to the static frontend origin. This is a browser wiring test, not a deployed Vercel integration.
- All 5 backend tests passed after separating the frontend connection.
- Caddy 2.11.7 validated `Caddyfile.ip`, including the explicit Let's Encrypt `shortlived` ACME issuer profile. The pinned `caddy:2.11.7-alpine` image is available. Docker Compose accepted the IP-certificate override configuration.
- No real IP certificate has been issued here, and no Vercel deployment, Oracle HTTPS endpoint or production CORS connection has been verified. Follow docs/VERCEL.md for actual deployment checks. GitHub publication status must be established from the push result, not inferred from a local commit.
