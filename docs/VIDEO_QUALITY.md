# Voice quality and matching visuals

In Create, choose **Aria · US English**, **Guy · US English** or **Sonia · British English** for a natural network voice. **Local English** is robotic eSpeak intended for offline tests. The sample-story button deliberately chooses the offline voice, so change the Narrator voice after loading a sample if you want natural speech. A failed natural voice now reports an error; it no longer silently substitutes eSpeak.

For moving stock footage, choose **Pexels / Pixabay stock media** and add a Pexels API key in Settings. Pixabay supplies photos as a fallback. Studio backgrounds are text cards, not topic-specific stock footage. Adding a Gemini key also enables short concrete visual search queries for each narration scene, even when you supply a manual script. Without Gemini, queries use the words of that scene, not the global video title.

The pipeline reads Edge's WordBoundary events and cuts at narration boundaries, preserving sentence changes when the transcript matches. Long sentences are divided into roughly four-second beats. Captions use the same word timestamps. Offline narration is generated in short sentence/chunk recordings whose real durations determine the scene boundaries; its word-level captions remain estimates within each scene.

Each scene searches for a distinct stock asset. A long stock clip is trimmed to that scene's duration; the pipeline does not divide the whole video equally among three assets. If a scene has no distinct stock match, it uses a card with that scene's text and reports the missing match in the project's warnings. It does not fill that scene with another scene's footage. Stock search results still depend on the provider's collection and ranking; this is not a guarantee of exact visual correspondence for every phrase.

The selected 15/30/60 seconds is a maximum. The complete narration determines the output length. If narration exceeds the maximum, the job fails before downloading stock and recommends a shorter script or a longer target; it never truncates the ending. Useful starting budgets are around 25–28 words for 15 seconds, 50–55 for 30 seconds and 105–110 for 60 seconds. Actual voice duration and pauses decide whether it fits.

Existing videos keep their previous rendering. Create a new video to use the new timing. Project metadata includes the actual voice, whether word timings came from the speech provider, and each scene's text, start/end times, visual query and source. Provider keys and working media paths are excluded from that metadata.

## Updating the Oracle backend

Wait for any current render to finish. From `~/faceless-studio-app`, run each command in order and stop if one fails:

```bash
git pull --ff-only
sudo docker compose -f compose.yaml -f compose.ip-https.yaml build studio
sudo docker compose -f compose.yaml -f compose.ip-https.yaml up -d --no-deps studio
curl --fail http://127.0.0.1:8000/health
```

The update preserves `.env`, project data and Caddy certificate volumes. The Vercel frontend can keep its current deployment because its request fields remain compatible. Verify a new natural-voice stock video from the real Vercel site. Provider availability on Oracle must be tested there; a local test with mocked stock/speech services does not establish live provider access.

## Natural voice connection errors

The backend pins `edge-tts==7.3.1`. Compared with the previous 7.2.7 pin, upstream added support for the speech service's Sec-MS-GEC v2 protocol, corrected clock-skew date parsing and improved timing compensation. Sources: [upstream changes](https://github.com/rany2/edge-tts/compare/7.2.7...7.3.1) and [7.3.1 release](https://github.com/rany2/edge-tts/releases/tag/7.3.1). The update addresses an obsolete-client compatibility risk; it does not establish why a specific Oracle request failed.

Use the update commands above to rebuild the backend; pulling source without rebuilding leaves the running container's old package installed. Check the actual running version with:

```bash
sudo docker compose -f compose.yaml -f compose.ip-https.yaml exec studio python -c 'import edge_tts; print(edge_tts.__version__)'
```

It should print `7.3.1`. Retry a short Aria narration. Errors now distinguish HTTP 403 rejection, HTTP 429 rate limiting, other HTTP failures, DNS lookup, timeout, connection failure and TLS verification. Only safe categories/status codes are exposed; raw provider exceptions, URLs and headers stay private. Aria, Guy and Sonia share a service endpoint, so switching the voice does not resolve a server-wide network or protocol failure. A continuing 403 needs actual Oracle diagnostics, including the installed version and synchronized server clock; do not assume that it proves an IP ban.
