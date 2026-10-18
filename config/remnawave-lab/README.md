# Isolated Remnawave integration lab

The lab pins official Panel backend `3.4.5` (image digest `sha256:b16d724b90fd7c9fec2df04bd28938a671cafc62894105068e11550ee3449c56`), Node `3.4.2` (digest `sha256:1f97485b4bc7e4944f1ae95cc57d176376813b0022e9567b705f384f1a2e909d`), PostgreSQL `18.4-alpine3.24` (digest `sha256:9a8afca54e7861fd90fab5fdf4c42477a6b1cb7d293595148e674e0a3181de15`), Valkey `9.0.2-alpine` (digest `sha256:68677f85c863830af7836ff07c4a13b7f085ebeff62f4dedb71499ca27d229f2`), and Caddy `2.10.2-alpine` (digest `sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d`). Panel/Node package versions were checked against official source manifests on 2026-10-16; backend `3.4.5` depends on `@remnawave/node-contract` `3.4.1`. The Panel/Node pair was then exercised with applied profiles, managed users, a real subscription, and an HTTP probe through both Nodes; see the verified result below. PostgreSQL tag `18.4-alpine3.24` is published by the official PostgreSQL image and uses the same PostgreSQL release as the official Panel compose's `18.4` tag. The standard `18.4` image pull stalled in this environment; this exact-version Alpine variant was pulled successfully.

The pinned Node image reports bundled Xray `26.7.28` (`5ca6f4b`, `go1.26.5`, linux/amd64). This is the Xray version used by live Node integration checks; the earlier standalone Xray `26.3.27` was only used by the pre-existing local harness.

This is a local test deployment, not a production recipe. Caddy terminates local TLS at `https://localhost:8443`; its host binding, the metrics endpoint, and the test Xray listeners bind only to loopback. DB, Valkey, and Node API ports are not published. Services share a dedicated Docker bridge network. It is not marked `internal`: Docker Desktop did not publish Panel ports while that flag was enabled. Containers may therefore have outbound internet access, but no service is bound to a public host interface. Caddy's internal CA is test-only and its data stays in a named Docker volume; browsers may need to trust that CA for a warning-free connection. Production must follow Remnawave's official reverse-proxy/TLS instructions and use a publicly valid certificate.

## Secret files

Keep all files below in `%LOCALAPPDATA%\JopesVPN\remnawave-lab`, outside this repository:

- `compose.env` — Compose interpolation values: `LAB_SECRET_DIR`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`.
- `panel.env` — Panel's `APP_PORT`, `METRICS_PORT`, `API_INSTANCES`, `DATABASE_URL`, `REDIS_SOCKET`, `APP_SECRET`, `FRONT_END_DOMAIN`, `SUB_PUBLIC_DOMAIN`, `METRICS_USER`, `METRICS_PASS`, `WEBHOOK_SECRET_HEADER`, and `SERVICE_SNI_VERIFICATION`.
- `bridge-node.env` and `exit-node.env` — Node-generated `SECRET_KEY` values and `NODE_PORT=2222`.
- `admin.txt` — one-time local super-admin login created for this test.

Generate random secrets locally; do not paste generated credentials into chat, source files, or logs. The first user registered in a new Panel becomes super-admin. Do not use a real email or password.

## Start / stop

From repository root (PowerShell), pass the external env file explicitly:

```powershell
$env:LAB_ENV = Join-Path $env:LOCALAPPDATA 'JopesVPN\remnawave-lab\compose.env'
docker compose --env-file $env:LAB_ENV -f config\remnawave-lab\docker-compose.yml up -d
docker compose --env-file $env:LAB_ENV -f config\remnawave-lab\docker-compose.yml ps
```

Open `https://localhost:8443` only from this host and create the disposable super-admin. The Caddy `tls internal` certificate is local-test-only; do not use this TLS setup outside this lab.

Create two Nodes in Panel. Set the Node address to `node-bridge` / `node-exit`, and the Node API port to `2222`. Copy each generated Node Compose `SECRET_KEY` directly into its external env file (do not log it); set `NODE_PORT=2222`. The Compose overlay keeps each Node API reachable only on the internal Docker network and maps its test Xray inbound to a distinct loopback host port. It intentionally replaces the official production `network_mode: host` setup for this isolated multi-node lab. Enable only the profile's matching inbound on each Node.

After adding both Node secrets, validate/start the overlay:

```powershell
docker compose --env-file $env:LAB_ENV -f config\remnawave-lab\docker-compose.yml -f config\remnawave-lab\docker-compose.nodes.yml config --quiet
docker compose --env-file $env:LAB_ENV -f config\remnawave-lab\docker-compose.yml -f config\remnawave-lab\docker-compose.nodes.yml up -d
```

Do not run `docker compose down -v` unless explicitly asked; it deletes the lab database. To stop containers without deleting test state, run `docker compose ... stop`.

## Scope and checks

Use `bridge-profile.json` and `exit-profile.json` from `config\remnawave\` as the Config Profiles. Replace placeholders only in Panel's lab profiles with test-only endpoints/keys. For bridge's outbound address use `node-exit` (Docker DNS), not the host-published port. Keep the profile's `clients` arrays empty; Panel must inject managed users.

## Verified live integration (2026-10-16)

- Panel 3.4.5 and both Nodes 3.4.2 were running; Nodes reported bundled Xray 26.7.28. Profiles and matching inbounds were applied through Panel.
- A Panel-managed exit service user was assigned to the exit inbound through an Internal Squad and referenced by the bridge outbound. A separate Panel-managed subscriber was assigned to the bridge inbound.
- Initial subscription had no Host mapped to the bridge inbound and contained invalid `0.0.0.0:1`, `security=none` endpoint fields. The initial `node-bridge:443` Host was then disabled because Docker DNS is not available outside the lab network. The current enabled test Host `Jopes Bridge Loopback Test` points to `127.0.0.1:18443` and `JOPES_BRIDGE_IN`; its link is usable only by a client on this same Windows host, not another device or a public network.
- A temporary Xray client config was generated from that actual Panel subscription outside the repository. Xray 26.7.28 `run -test` returned `Configuration OK`; an HTTPS request through the local SOCKS listener to `example.com` returned HTTP 200.
- Panel user counters went from 0 to 197 bytes for both the bridge subscriber and exit service user; both were online. This is evidence that the single test request passed through the real bridge and exit Nodes, not merely a hand-built Xray-only chain.
- A later actual subscription fetch through Panel returned HTTP 200, `text/plain; charset=utf-8`, with a Base64 body decoding to one VLESS + REALITY link for `127.0.0.1:18443`; it contained no Docker hostname. A nonexistent subscription ID returned 404. This validates same-host endpoint response, not client handshake or connection.
- A disposable Panel user was created, subscribed, disabled, and deleted through the Panel UI. While disabled, its subscription endpoint still returned HTTP 200 with a `DISABLED` marker; Xray authentication denial after disable was not tested.
- Both Nodes were restarted and returned to 2/2 online. Stopping exit resulted in 1/2 online; stopping bridge made its published loopback listener unavailable. Both recovered after start. These were node health/listener checks, not tunneled requests during failure.
- Temporary subscription/client config and receiver data were removed. Caddy's temporary `/_lab/*` file route and its transfer-folder mount were removed; the old route returned 404 in the browser and the transfer folder is empty. The test Xray client container was removed. Panel/Node lab containers and four named volumes remain.

For repeatable diagnostics and safe stop/start commands, use the Compose commands in this file. Compose uses `restart: "no"`; after a Docker Engine restart, manually run the `up -d` commands. Do not use `down -v`.

Detailed outcome and limits are recorded in the verification section above.

Happ import/connect, DNS/leak behavior, client tunnel behavior when bridge is stopped, external bridge reachability, and whitelist bypass were not tested. A real Xray client did pass HTTPS through the Panel-managed bridge → exit route and failed closed/recovered when the exit Node was stopped/restarted; this is recorded in the verification section above. This lab cannot prove whitelist bypass. The existing local Xray-only failure/recovery tests remain separate and do not replace the live Panel/Node check.
