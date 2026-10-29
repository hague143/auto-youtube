# Validation — 2026-10-28 rebuilt private browser edition

Historical checks below describe earlier revisions. See the dated sections at the end for later fixes and deployment observations.

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

## Vercel Python-install failure fix

The first real Vercel build of commit 4e3cce0 attempted to install the Oracle backend under Python 3.14.8. Its pinned pydantic-core/PyO3 dependency supports at most Python 3.13, so installation failed before the static build. Backend dependencies were not upgraded or given ABI bypass flags.

The frontend now lives in the independent `web` project, with only Node/static source, its own lockfile, and explicit npm install/build commands in `web/vercel.json`. The Vercel project Root Directory must be `web`, Output Directory `dist`, with source inclusion outside that root disabled. Root npm commands still delegate to this project. Docker uses the same canonical `web/src` files through a root build context restricted by `.dockerignore`; the Python backend remains on its pinned Python 3.12 image.

Validation after isolation: **4 frontend tests passed**, including copying only the web project into an isolated temporary directory, performing a clean npm install and building with no parent/backend source available. All **5 backend tests passed**. The exported frontend's separate-backend browser smoke and the real Docker/eSpeak/H.264/AAC rendering smoke passed again. Compose accepted the updated root build context. The new project-root settings still need to be applied and redeployed in the user's Vercel dashboard; local checks do not establish a successful hosted deployment.

## Narration and scene timing — 2026-10-29

The user confirmed that the actual Oracle HTTPS `/health` endpoint returned `status: ok` after the IP TLS fix, and that the earlier pipeline creates videos from the connected frontend. These observations do not validate the new narration/scene code on Oracle.

- **16 backend tests passed**, including an actual H.264/AAC render where two eight-second stock fixtures are cut to unequal 0.5-second and 1.5-second narration scenes. Decoded red/blue frames verify the visual switches at the intended boundary; the export lasts two seconds and its SRT preserves supplied word times.
- Other tests cover sentence boundaries, short scenes with all script words retained, per-scene stock queries, Gemini plans returned out of order, invalid-plan fallback, missing stock results, removal of partial downloads on budget exhaustion, measured offline scene durations, natural-voice timing fixtures, refusal to silently substitute eSpeak, and refusal to truncate overlong narration.
- The Docker image built with the new timeline module in the restricted build context. The real browser/container smoke passed again with actual eSpeak audio, contiguous measured scene metadata, MP4 preview/download, credential clearing, project listing, mobile layout and deletion. Its local output lasted **6.370 seconds**.
- A live Aria request in this cloud failed with `ClientConnectorDNSError`. Natural voice timing tests use explicit provider fixtures; live Edge speech and real Pexels/Gemini relevance must be checked on Oracle after updating. Optional Whisper was not installed or run.
- Existing missing frontend files in the shared checkout were preserved. Validation used a source-only temporary copy of committed frontend files plus the changed backend/tests. This change does not remove frontend files or require a new Vercel build.
- Existing upstream Starlette/AnyIO and MoviePy/NumPy deprecation warnings remain. No dependency versions or TLS verification settings were relaxed.

## Edge connection compatibility — 2026-10-29

- The user's Oracle render reported the previous generic natural-voice/timing failure. A subsequent direct SDK diagnostic on Oracle confirmed `WSServerHandshakeError`, **HTTP 403**, before the 7.3.1 upgrade. This establishes service rejection; it does not establish an Oracle IP block or prove the precise rejection reason.
- The old `edge-tts==7.2.7` pin was upgraded to the maintainer's current `7.3.1` release. Upstream changes add the service's v2 connection protocol and fixes for clock-skew parsing and timing compensation. The existing voice/timing API remains compatible.
- **20 backend tests passed**, including redacted HTTP 403/429/503 diagnostics, timeout classification, word-timing fixtures and the real stock-scene FFmpeg export. Docker built successfully; its installed Edge version was independently checked as **7.3.1**. The real browser/container render smoke passed with measured scene metadata, MP4 preview/download, persistence, mobile layout and deletion.
- A live request with the new package in this cloud still failed at DNS lookup; the new diagnostic correctly identified that category without printing the raw exception. Live synthesis on Oracle remains unverified until the user rebuilds and retries there. A package update and passing fixtures are not proof of a successful Microsoft service connection.

## ElevenLabs integration — 2026-10-29

- Added an explicit ElevenLabs narrator, masked runtime API-key field, Voice ID field and a read-only key test that offers the account's available voices. The backend sends one synthesis request with `eleven_multilingual_v2` to the documented `/with-timestamps` API. Audio and normalized character alignment from that response drive scene cuts and captions together. No paid synthesis retry or fallback is automatic.
- **34 backend tests passed**. ElevenLabs fixtures cover the exact request/header/model contract, character-to-word timing, unequal scene boundaries, malformed alignment, HTTP key/permission/voice/credit/rate errors, key requirements, Voice ID path rejection, read-only voice listing and a complete API job exporting a real H.264/AAC MP4. The job's persisted metadata and final artifacts exclude the provider key and intermediate audio.
- **4 frontend build tests passed**, including a clean isolated web install/build. The static-export browser check passed with the ElevenLabs key test, account Voice ID selection, applied runtime settings and a create request carrying the chosen provider/key/Voice ID only to the separate HTTPS backend. Credential fields clear on refresh; no API calls go to the frontend origin.
- Docker built successfully; the existing actual eSpeak/browser export smoke passed again. Responsive settings/layout tests passed at 1440, 1024, 768, 600, 390 and 320 pixels, including the new masked key field. Desktop tilt, reduced motion and touch checks passed.
- ElevenLabs contracts were checked against the provider's maintained SDK types and request implementation. No real ElevenLabs API key was supplied to this cloud, no live ElevenLabs speech request was made and no account credits were used by these tests. Real voice access, quota and quality remain to be checked on Oracle using the user's key entered privately in the app.
- Frontend edits were validated in an isolated source copy, preserving the pre-existing missing files in the shared checkout. Only the intended app.js/index.html edits are included in the published change; unrelated frontend deletions are excluded.
