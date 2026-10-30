# Voice quality and matching visuals

## ElevenLabs

1. Connect your rendering server in Settings as before. Paste your ElevenLabs key into the masked **ElevenLabs API key** field. Do not put this key in the server access token field, source code or Vercel build variables.
2. Press **Test** next to the key to read your account's voices without synthesizing speech. The **ElevenLabs Voice ID** field offers the returned IDs and names. If the initial default ID is absent, it selects an available voice. You can also paste a specific Voice ID yourself.
3. Press **Save settings**, then choose **ElevenLabs · your API key** under Narrator voice in Create. Start with a short English script and Script captions. The sample-story button deliberately selects the offline voice, so choose ElevenLabs again after loading a sample.

Keys need **Text-to-Speech** permission. The optional key test also needs **Voices read** permission; TTS-only keys can generate with a valid Voice ID even when that read check is denied. Rendering uses `eleven_multilingual_v2` and the `/v1/text-to-speech/{voice_id}/with-timestamps` endpoint with MP3 output. Its audio and character alignment come from the same synthesis request. Character times are grouped into spoken words for both scene cuts and captions, preferring normalized alignment when provided.

Your ElevenLabs allowance/credits apply. The app sends one synthesis request for short narration. Longer scripts are split at sentence boundaries into parts of at most about 2,000 characters, with one request per part. Audio is decoded to a common PCM format and joined; each part’s actual decoded duration offsets its word cues. It does not automatically retry paid synthesis. A failed later part does not resume or recover earlier paid parts on a new render. A provider request may consume credits even if a later duration check or video export fails. Check a short narration first. HTTP errors distinguish invalid keys, missing voice permissions, unavailable Voice IDs, credit exhaustion and rate limits without exposing raw provider responses. No fallback voice is substituted.

Press Save settings to persist the key, server connection and Voice ID in this browser. An encrypted IndexedDB record uses a non-extractable browser key; the browser profile can still use the saved credentials. Refreshing restores them. Change a field and save to replace it; Clear saved settings removes the stored record. The key is sent only to your own server for the requested operation. It is forwarded to ElevenLabs using the `xi-api-key` header and is not written into project metadata or the static frontend. Public deployment requires your normal trusted HTTPS backend connection.

## Edge and offline voices

In Create, choose **Aria · US English**, **Guy · US English** or **Sonia · British English** for a natural network voice. **Local English** is robotic eSpeak intended for offline tests. The sample-story button deliberately chooses the offline voice, so change the Narrator voice after loading a sample if you want natural speech. A failed natural voice now reports an error; it no longer silently substitutes eSpeak.

For moving stock footage, choose **Pexels / Pixabay stock media** and add Pexels and/or Pixabay API keys in Settings. Both providers now supply videos and photos. Oversized videos are skipped in favor of smaller files, another candidate/provider or a matching photo. Studio backgrounds are text cards, not topic-specific stock footage. Adding a Gemini key also enables short concrete visual search queries for each narration scene, even when you supply a manual script. Without Gemini, queries use the words of that scene, not the global video title. See [stock limits and fallback](STOCK_MEDIA.md).

The pipeline reads Edge's WordBoundary events and cuts at narration boundaries, preserving sentence changes when the transcript matches. One visual sentence normally remains one scene. Sentences longer than 24 words or about 12 seconds are divided into bounded beats without discarding words. Captions use the same word timestamps. Offline narration is generated in short sentence/chunk recordings whose real durations determine the scene boundaries; its word-level captions remain estimates within each scene.

Each scene searches for a distinct stock asset. A long stock clip is trimmed to that scene's duration; the pipeline does not divide the whole video equally among three assets. If a scene has no distinct stock match, it uses a card with that scene's text and reports the missing match in the project's warnings. It does not fill that scene with another scene's footage. Stock search results still depend on the provider's collection and ranking; this is not a guarantee of exact visual correspondence for every phrase.

The selected 15/30/60 seconds or 2/5/10/15 minutes is a maximum. The complete narration determines the output length. If narration exceeds the maximum, the job fails before downloading stock and recommends a shorter script or a longer target; it never truncates the ending. Useful starting budgets are around 25–28 words for 15 seconds, 50–55 for 30 seconds and 105–110 for 60 seconds. Actual voice duration and pauses decide whether it fits.

## Long videos and relevant stock

Generated long scripts use a connected chapter outline, then up to three minutes of narration per writing request. Prompts ask for concrete visible subjects/actions and one visual idea per sentence. A 15-minute target starts around 1,710 generated words; actual voice speed may produce a shorter movie. It does not pad footage to fill the target. Manual scripts support up to 20,000 characters.

Gemini search planning runs in batches of 20 measured scenes, with neighbouring sentences to resolve pronouns. Each scene gets a specific search and up to two equivalent shorter searches. Both stock providers are tried for videos across those searches before photo fallback. Candidate descriptions, tags and URL slugs are ranked against the query, and clearly unrelated metadata is rejected. This does not inspect or understand every frame of a downloaded clip. Generic search metadata can still return an imperfect match. Review the project's **Scene timeline** and source links; provider collection/ranking and quotas still apply. Without Gemini, the app uses per-scene keywords and reports that limitation.

The renderer runs one single-threaded FFmpeg scene encoder at a time, with local ASS captions, then joins the uniform H.264 scenes and muxes the full narration. It does not open hundreds of video decoders or retain a separate image layer for every caption. Scene boundaries use cumulative 24 fps frame positions so rounding does not accumulate across a long movie. A visual switch can differ from a speech boundary by up to one frame. Working scene files are removed on completion or encoding failure. Downloaded stock and working narration are cleaned after success or failure.

A 15-minute submission requires about 2.64 GiB free disk as an admission check. The automatic stock transfer allowance scales from 192 MiB for short videos to 1,350 MiB for a 15-minute target, with additional free-disk headroom. An explicit STUDIO_STOCK_TOTAL_MB overrides the automatic value. Only one render runs at a time. Check a 1-minute stock project, then a 5-minute one on the actual Oracle VM before requesting 15 minutes; CPU/network/provider quotas determine time. Refreshing restores saved settings and running project progress. A server restart still marks an unfinished job failed; durable job resumption is not implemented.

Existing videos keep their previous rendering. Create a new video to use the new timing. Project metadata includes the actual voice, whether word timings came from the speech provider, and each scene's text, start/end times, actual visual query, media kind, provider and source link. Provider keys and working media paths are excluded from that metadata.

## Updating the Oracle backend

Wait for any current render to finish. From `~/faceless-studio-app`, run each command in order and stop if one fails:

```bash
git pull --ff-only
sudo docker compose -f compose.yaml -f compose.ip-https.yaml build studio
sudo docker compose -f compose.yaml -f compose.ip-https.yaml up -d --no-deps studio
curl --fail --retry 5 --retry-connrefused --retry-delay 2 http://127.0.0.1:8000/health
```

The update preserves `.env`, project data and Caddy certificate volumes. ElevenLabs also needs the updated `web` frontend: deploy the latest main commit in Vercel with Root Directory `web`, then refresh after deployment is Ready. Older Edge/local requests remain compatible with the backend. Verify a new natural-voice stock video from the real Vercel site. Provider availability on Oracle must be tested there; a local test with mocked stock/speech services does not establish live provider access.

## Natural voice connection errors

The backend pins `edge-tts==7.3.1`. Compared with the previous 7.2.7 pin, upstream added support for the speech service's Sec-MS-GEC v2 protocol, corrected clock-skew date parsing and improved timing compensation. Sources: [upstream changes](https://github.com/rany2/edge-tts/compare/7.2.7...7.3.1) and [7.3.1 release](https://github.com/rany2/edge-tts/releases/tag/7.3.1). The update addresses an obsolete-client compatibility risk; it does not establish why a specific Oracle request failed.

Use the update commands above to rebuild the backend; pulling source without rebuilding leaves the running container's old package installed. Check the actual running version with:

```bash
sudo docker compose -f compose.yaml -f compose.ip-https.yaml exec studio python -c 'import edge_tts; print(edge_tts.__version__)'
```

It should print `7.3.1`. Retry a short Aria narration. Errors now distinguish HTTP 403 rejection, HTTP 429 rate limiting, other HTTP failures, DNS lookup, timeout, connection failure and TLS verification. Only safe categories/status codes are exposed; raw provider exceptions, URLs and headers stay private. Aria, Guy and Sonia share a service endpoint, so switching the voice does not resolve a server-wide network or protocol failure. A continuing 403 needs actual Oracle diagnostics, including the installed version and synchronized server clock; do not assume that it proves an IP ban.
