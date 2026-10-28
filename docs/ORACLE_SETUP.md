# Oracle setup status and DNS

Verified by the user on 2026-10-28: SSH to the Ubuntu ARM instance succeeds, Docker hello-world runs, nginx responds locally, and the public IP serves the nginx page after inbound TCP 80 was added to the attached security list. Ports 80/443 had initially been added only as egress rules. Outbound rules do not allow inbound web connections.

## Free subdomain

1. Open the official https://www.duckdns.org/ website in your own browser and sign in using one of its available options.
2. Add an available subdomain name. The actual result ends with `.duckdns.org`.
3. Set the domain's current IPv4 to your Oracle VM's current public IP. Do not leave it set to the PC's public IP detected during sign-in. Save with Update IP.
4. Confirm the exact hostname resolves to the VM public IPv4, for example with Windows `nslookup YOUR_ACTUAL_NAME.duckdns.org` after replacing the placeholder.
5. Keep the DuckDNS update token private. The app and Caddy do not need this token for ordinary HTTP certificate validation.

The user reported having no domain. No DuckDNS name has yet been supplied or created by this cloud session. No app deployment or HTTPS certificate on the Oracle VM has been verified. The nginx container is a network test, not the video application. Leave it running until the actual replacement deployment is ready.

## Source transfer

`python3 scripts/package.py` creates a source-only ZIP plus a checksum-verified copy/paste extraction command under `artifacts/`. Both exclude `.env`, credentials, data and caches. The extraction command refuses to overwrite an existing application directory and generates a private token on the machine where the user runs it. It does not change Docker containers or start the app. A local artifact path is not a downloadable attachment in this client; provide the command text or a verified real download URL.

The deployment sequence after DNS and source transfer is described in README.md. Validate local `/health`, public HTTPS `/health`, authenticated capabilities, one real render and an MP4 download on the actual ARM VM. Successful local cloud tests are not evidence of Oracle deployment.
