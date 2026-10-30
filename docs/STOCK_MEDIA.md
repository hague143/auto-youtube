# Stock sources and download limits

The earlier `Stock download limit reached` message came from the app's own 40 MiB per-file / 192 MiB per-render transfer limits. It did not establish that a stock provider had exhausted its API quota. The app had stopped at the first oversized file instead of trying another asset.

Gemini plans contextual scene searches in batches, including equivalent shorter searches. Candidate tags, descriptions and URL slugs are ranked against the spoken subject/action; clearly unrelated metadata is skipped. All equivalent video searches run before photo fallback. This is metadata/search matching, not visual recognition of every frame.

The updated flow for each narration scene is:

1. Search Pexels videos and prefer short source clips and suitable HD/SD files up to a 1920-pixel long edge. A fair share of the remaining transfer allowance is used when choosing a file, so the first scenes do not consume the whole allowance.
2. Skip files whose advertised size is too large before downloading their body. If size is unknown, enforce the byte limit while streaming. Remove partial files and account for transferred bytes even when an attempt fails. Try up to four video downloads per provider for that scene.
3. Try the Pixabay videos API when Pexels candidates are unsuitable or unavailable. If neither provider supplies a usable video, try matching Pexels/Pixabay photos. A portion of the transfer allowance is reserved for those photos.
4. If stock still cannot fit, complete that scene with its narration text card and show a warning. Timing stays tied to the original narration. Budget exhaustion or an oversized stock candidate does not automatically restart paid narration or fail the whole render.

Downloaded MP4s are checked for a readable bounded video stream; images are checked before assembly. HTTPS verification, provider host allowlists and the restriction on redirects remain enabled. Provider search failures disable further failed API attempts to that provider for the current render while allowing another source to work.

API search responses are cached for 24 hours in the private data volume's `.stock-cache` directory. Cache fingerprints are HMACs tied to the credential, endpoint and query; raw credentials/request URLs are not stored. Any reflected credential is scrubbed from response metadata. The cache keeps at most 2,048 result files, each capped at 2 MiB, with a 128 MiB combined cache cap. Source video files are not retained in this cache. [Pixabay's API documentation](https://pixabay.com/api/docs/) requires search caching and describes its rate limits and permitted use; caching does not remove those rules.

## Available sources

| Source | Media | App support |
| --- | --- | --- |
| [Pexels](https://www.pexels.com/api/) | Free stock videos and photos | API key in Settings |
| [Pixabay](https://pixabay.com/api/docs/) | Free stock videos and photos | API key in Settings; videos are now supported |
| [Mixkit](https://mixkit.co/free-stock-video/) | Selected free stock clips under the applicable asset license | Manual downloads; not integrated in this app |

Free stock is subject to provider quotas, availability and the applicable license. There is no unlimited-API promise. The app preserves provider/creator links in project media credits.

## Owner configuration

The default per-video limit remains 40 MiB, with photos capped at 8 MiB. Automatic per-render allowance is max(192 MiB, 1.5 MiB × target seconds): 192 MiB for short videos and 1,350 MiB for 15 minutes. Explicit owner limits replace that automatic value. These are application resource limits. Owner overrides can be added to the Oracle `.env`, for example:

```dotenv
STUDIO_STOCK_ASSET_MB=80
STUDIO_STOCK_TOTAL_MB=512
```

Limits are bounded at 256 MiB per video and 4096 MiB per render. The actual transfer allowance also reserves disk headroom of 512 MiB plus 0.5 MiB per target second above 60 for scene exports and final output. Recreate the studio service after changing environment values. Increasing the allowance can increase bandwidth/storage use; it does not increase a provider's API quota. Normal use should try the optimized defaults first.

Update the Oracle backend using the commands in [video quality](VIDEO_QUALITY.md#updating-the-oracle-backend). In the app, add a Pixabay key alongside Pexels and save settings to enable both sources. The browser remembers encrypted settings across refreshes until edited or cleared; existing `.env`, server token, projects and HTTPS certificates are preserved by an ordinary backend update.
