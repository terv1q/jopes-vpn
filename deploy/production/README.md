# Jopes VPN production deployment (single Germany VPS)

Production snapshot, 2026-10-24 (recorded in `OpenCode.md`): all eight containers are running; Telegram registers 65 handlers. Stars has three fulfilled orders from October 19–21, and Platega credited a real 1 ₽ top-up on October 24. Hysteria 2 is active on UDP 443 alongside TCP/REALITY 2053 and XHTTP/REALITY 8443. The setup instructions below describe initial provisioning; those services are already enabled on the existing VPS.

This deployment is installed on the Germany VPS and was exercised there on 2026-10-17, but it remains an owner-operated single-VPS setup rather than a finished commercial service. It uses the exact Remnawave versions that passed the project's local Panel/Node integration test: Panel `3.4.5`, Node `3.4.2`, PostgreSQL `18.4`, Valkey `9.0.2`, and Caddy `2.10.2`; those infrastructure image digests are pinned in Compose. The bot image is based on the exact Python `3.13.15-slim-bookworm` tag, not a registry digest.

Remnawave owns users, subscriptions, Nodes, and Xray configuration. The bot uses the Panel HTTPS API; it does not keep a second user database or edit Xray config. The Germany Node is the first direct VPN exit. The existing separate bridge/exit profiles remain the future topology; this deployment does not implement a bridge or whitelist circumvention.

## Domains and DNS

Create only these records after the VPS has a stable public IPv4:

| Type | Name | Value | Use |
|---|---|---|---|
| A | `remnawave` | `<VPS_PUBLIC_IPV4>` | Panel UI, bot API calls, and Panel subscriptions over HTTPS |
| A | `connect` | `<VPS_PUBLIC_IPV4>` | Direct VLESS + REALITY TCP inbound on port `2053`, Remnawave Subscription Page, and the ticket portal under `/support/` over HTTPS `443` |

Do not create `api.example.com`: there is no separate Jopes API. Do not create `sub.example.com`: the Panel publishes `/api/sub` on `remnawave.example.com`. Caddy terminates normal HTTPS for that web host only. VLESS + REALITY is not sent through Caddy; clients connect directly to `connect.example.com:2053/TCP`. Keep `connect` DNS-only (not an HTTP/CDN proxy). Do not add AAAA records until IPv6 routing, firewall, and client connectivity have been tested on the VPS.

## Ports and access boundaries

- Public: SSH from the owner's trusted source IP only; TCP `80` and `443` for Caddy/ACME, Panel, and the subscription page; TCP `2053` for the raw VLESS + REALITY inbound. TCP `8443` is public only if the XHTTP inbound is deployed.
- Not public: PostgreSQL, Valkey, Panel's internal app/metrics ports, and Node API `2222`.
- Node uses the official host-network Compose pattern. Panel has a static address `172.30.50.10` on the Compose bridge; its host gateway is `172.30.50.1`. Register the Node in Panel with address `172.30.50.1` and API port `2222`. Permit `2222/TCP` in UFW only from `172.30.50.10`; do not add a provider-firewall/public allow rule for it. Confirm both Panel-to-Node connectivity and external rejection of `2222` after deployment. If the host firewall does not enforce this source restriction with Docker's rules, stop and correct the firewall before operating; do not make the Node API public as a workaround.
- The profile listens on `2053/TCP` so it does not conflict with Caddy's web `443/TCP`. The Panel Host must advertise `connect.example.com:2053` and map to `JOPES_EXIT_IN`.

The Docker `data` network is marked internal. PostgreSQL has a named data volume. Valkey uses only a shared Unix socket volume for Panel/cache access and has no TCP listener or persistence; it is not an independent source of user data. Caddy certificate/account state and config use named volumes. The bot and ticket forum share the named volume `jopesvpn-production-bot-data`, mounted at `/app/data`; Panel remains the source of truth for accounts, subscriptions, and devices. Ticket messages, capability-token hashes, notification queue entries, and Telegram photo `file_id` values are stored in the same SQLite database. The forum is published only by Caddy under `https://connect.example.com/support`; its container has no host port. Users manage tickets in Telegram; `/support/t/...` is disabled. The administrator portal uses a separate password and signed, secure cookies. Web replies record the connecting IP, visible to the owner in the admin portal; source IPs are removed after 90 days, while ticket messages remain until the owner removes/backs up the database. Bot-originated replies have no client IP because Telegram does not provide it to the bot. Draft/reply prompts use in-memory FSM state and are lost on bot restart; saved ticket conversations persist. Do not use `docker compose down -v`.

## Before first boot

1. Provision a supported Ubuntu LTS VPS in Germany and a stable public IPv4. Add the DNS records above. Initially keep both names DNS-only.
   Remnawave's published requirements list Panel minimum 2 CPU cores/2 GB RAM/20 GB storage (recommended 4 cores/4 GB) and Node minimum 1 core/1 GB. Co-locating them has not been benchmarked; budget at least the component recommendations plus OS/container overhead and monitor memory/disk before admitting users.
2. Install Docker Engine and the Compose plugin using the official Docker/Remnawave-supported procedure. Confirm the package supports Compose profiles and health-conditioned `depends_on`.
3. Transfer the project source to the VPS using the owner's private repository or a secure file-transfer method, then work from `/opt/jopesvpn/deploy/production`. The current workspace has no Git remote, so no clone URL is known. Exclude `.env`, `.env.node`, `.venv`, lab secret files, and runtime data from the transfer.
4. Ensure `172.30.50.0/24` does not overlap an existing Docker, host, or provider network. If it does, choose a free `/24` and consistently update the Compose subnet, Panel address/gateway, and firewall rule.
5. Configure key-based SSH access and verify a second session works before disabling password/root login. Do not apply SSH changes that could lock out the owner.
6. Configure both provider firewall and host firewall. Replace `<OWNER_ADMIN_IP>` and `<SSH_PORT>` with the actual values; keep the existing SSH session open while enabling UFW:

   ```bash
   sudo ufw default deny incoming
   sudo ufw default allow outgoing
   sudo ufw allow from <OWNER_ADMIN_IP> to any port <SSH_PORT> proto tcp
   sudo ufw allow 80/tcp
   sudo ufw allow 443/tcp
   sudo ufw allow 2053/tcp
   sudo ufw allow from 172.30.50.10 to any port 2222 proto tcp
   sudo ufw enable
   sudo ufw status numbered
   ```

   The provider firewall should allow the same public ports, except `2222`. During the first Panel admin bootstrap, keep provider TCP `443` restricted to the owner's IP while allowing TCP `80` for ACME HTTP validation; after creating the owner account and closing open registration, allow public TCP `443`. Check provider-specific IPv6 firewall behavior before enabling IPv6. Hysteria 2 additionally requires public UDP `443`; do not open it until the Hysteria 2 inbound is configured.
7. Create the private environment file and replace placeholders required for the core Panel/DB/Valkey/proxy services:

   ```bash
   cp .env.example .env
   chmod 600 .env
   ```

   Use distinct random values for `POSTGRES_PASSWORD`, `APP_SECRET`, `METRICS_PASS`, and `WEBHOOK_SECRET_HEADER`. Generate random hex values locally, for example `openssl rand -hex 32`; use hex for the PostgreSQL password so the URL in Compose needs no URI escaping. Do not put secrets in shell history or terminal transcripts.
8. Use the real Telegram bot token and numeric owner ID for `BOT_TOKEN` and `BOT_ADMIN_IDS`. `PANEL_API_TOKEN` and `PANEL_INTERNAL_SQUAD_UUID` are filled after Panel initialization; do not start the bot profile before then. The Node `SECRET_KEY` is generated by Remnawave, not invented locally.

## First deployment

Run from `/opt/jopesvpn/deploy/production`. The `.env` file is required explicitly; never print it or use `docker compose config` without `--quiet` because expanded configuration contains secrets.

1. Start the Panel dependencies and Panel:

   ```bash
   docker compose --env-file .env -f docker-compose.yml up -d remnawave-db remnawave-redis remnawave
   docker compose --env-file .env -f docker-compose.yml ps
   ```

2. After DNS and ports `80/443` are reachable, start Caddy and wait for the public certificate:

   ```bash
   docker compose --env-file .env -f docker-compose.yml up -d remnawave-proxy
   curl --fail --silent --show-error https://remnawave.example.com/health
   ```

   The Panel's first account is an administrative bootstrap. Create only the owner's account, then disable any open registration using the Panel's supported admin controls. Confirm HTTPS/login before proceeding. Do not enable public Caddy proxying or weaken TLS verification.

3. In Panel, create one Node with API address `172.30.50.1` and port `2222`. Use the installation/environment values generated by this exact Panel version. Put the full generated Node environment into the ignored `.env.node` file (`chmod 600 .env.node`); `.env.node.example` only documents the expected minimum for the pinned Node release and is not runnable.
4. Copy `remnawave/germany-exit-profile.json` into a Panel Config Profile. In Panel, replace all `REPLACE_WITH_*` fields with newly generated production REALITY values and the selected valid target/SNI. Preserve the empty `clients` array so user accounts remain Panel-managed. Apply the profile to the Germany Node and enable `JOPES_EXIT_IN`. Do not reuse any lab key; prior lab key material is considered exposed.
5. Create an Internal Squad that exposes only `JOPES_EXIT_IN` and note its UUID for `PANEL_INTERNAL_SQUAD_UUID`. Create a Panel Host for that inbound with public address `connect.example.com` and port `2053`. Set `SUB_PUBLIC_DOMAIN=remnawave.example.com/api/sub`; subscription URLs remain on the HTTPS Panel host.
6. Start the Node with the official host-network Compose pattern and generated env file:

   ```bash
   docker compose --env-file .env -f docker-compose.yml -f docker-compose.node.yml --profile node up -d remnawave-node
   ```

   Verify that the Panel reports the Node connected. Verify from outside that TCP `2222` is not reachable and TCP `2053` is reachable. If the Node cannot connect to Panel at `172.30.50.1:2222`, diagnose the host route/firewall; do not open `2222` publicly.

7. Create a scoped Panel API token for the bot with only the already-confirmed 3.4.5 scopes needed by the code: `users:by-username`, `users:create`, `users:disable`, `users:enable`, `users:revoke-subscription`, `users:extend`, `nodes:list`, `hwid-user-devices:list-by-user`, and `hwid-user-devices:delete`. When the Subscription Page is deployed, the same token also needs `subscriptions:subpage-config` and `system:metadata`; the page container uses it to fetch page configuration and connect to Panel. Traffic packs additionally require `users:update` for `PATCH /api/users`. Keep `ENABLE_TRAFFIC_PLANS=0` unless that scope has been added to the scoped token and Panel restarted so cached scopes reload. Set it and the Squad UUID in `.env`. Do not give the bot an Admin JWT or broad administrator token.
8. Before starting the bot profile, set distinct random values for `TICKET_ADMIN_PASSWORD` (at least 20 characters) and `TICKET_SESSION_SECRET` (at least 32 characters). Keep both only in `.env`; do not put them in source control, terminal transcripts, or chat. The password is used for the web admin portal, and the session secret signs its 12-hour secure cookies.
9. Build/start the bot and private ticket forum only after Panel HTTPS, token, Squad, Telegram, and forum credentials are in place:

   ```bash
   docker compose --env-file .env -f docker-compose.yml --profile bot build bot ticket-forum
   docker compose --env-file .env -f docker-compose.yml --profile bot up -d bot ticket-forum
   docker exec jopesvpn-production-proxy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
   docker exec jopesvpn-production-proxy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
   curl --fail --silent --show-error https://connect.example.com/support/
   docker compose --env-file .env -f docker-compose.yml ps
   ```

   The forum has no host-published port: Caddy serves `https://connect.example.com/support/` and redirects to the admin login. Admin pages require the existing password and signed session; `/support/t/...` user capability routes are disabled. `BOT_ADMIN_IDS` defines administrator notifications. Keep the portal password only in `.env`; do not disclose it in chat or logs. `container_name` and one Compose service provide a single polling process. Do not run a second bot container or a manual polling command concurrently. Do not perform grant/Disable/Revoke tests against real users; use a disposable Panel user only after explicitly authorizing that test.

   The free trial is enabled by default for 3 days (72 hours): the bot checks that the user subscribes to `@jopesvpn`, creates the Panel user once with the `TRIAL` tag, and refuses a repeat trial because the Panel account already exists. The bot must remain an administrator of the channel. Override `TRIAL_CHANNEL_USERNAME` or `TRIAL_DAYS` in `.env` only if the owner changes the channel or duration. The trial uses the same Internal Squad as admin grants, so the API token needs `users:create`.

   The bot uses the HTTPS Subscription Page; do not add a `happ://` inline button because Telegram rejects that URL protocol. It does not print raw credentials in chat text. Referral attribution/rewards and the inactivity-only trial nudge are deployed: +3 trial days for the referred user, +10 for the inviter (60 inviter-reward days/month cap), and one nudge after 24 hours. The home menu starts new users in a persisted setup and keeps secondary sections in a one-column menu. Time-based and 3/10/30 GB Stars plans are listed in `jopesvpn/plans.json`; traffic plans remain hidden by default and require `users:update` before `ENABLE_TRAFFIC_PLANS=1`. Tickets are bot-only for users; close suggestions follow an admin response after 12 hours, ratings 1–5 follow final close, and closed tickets cannot be reopened. Actual import behavior still requires a real Telegram/Happ device test. No real payment was used to test fulfillment or refunds.

   Admin grants through `/admin_grant` create the Panel user with the `ADMIN` tag. The bot shows it as `🎫 Основной (ADMIN)`; the trial is shown as `🎁 Пробный (trial)`. Tags are written only on creation, so existing Panel users keep their current tag.

   Admin panel `📡 Логи подключений` toggles connection alerts in the admins' private chats. State is stored in SQLite and shared by all `BOT_ADMIN_IDS`. When enabled, a background task baselines `known_users()` silently, then polls every 60 seconds via `GET /api/users/by-username/{username}` and `GET /api/hwid/devices/{userId}`. New `(hwid, requestIp)` endpoints (or endpoints older than 24 hours) and changed `userTraffic.onlineAt` (30-minute cooldown) produce a message with Telegram account, Panel ID, tariff, status, expiry, platform/OS/model, `userAgent`, HWID, IP, Node UUID, and time. Buttons open the profile, Subscription Page, device list, disable/enable confirmation, and log toggle. IP/client come from HWID subscription-request metadata, not packet-level VPN sessions. Coverage is limited to users who have started the bot because the current token lacks global connection-job scopes (`403`); delivery is best-effort. Do not copy IP/HWID values from these alerts into logs or public reports.

## Platega.io payments

Telegram Stars remains available for plan purchases and wallet top-ups. When both `PLATEGA_MERCHANT_ID` and `PLATEGA_SECRET` are configured, the plan and top-up screens additionally offer the documented one-off methods (SBP QR, ERIP, card, international, crypto, SberPay) without provider branding. The wallet minimum is 1 RUB, verified by creating an unpaid SBP invoice against the configured merchant. Top-up buttons contain their quoted amount and remain usable after navigation or a bot restart. Pending invoices are reused only for the same payment method and before their saved expiry.

Set the callback URL in Platega's merchant settings to `https://<CONNECT_DOMAIN>/platega/callback`. Caddy forwards it to the bot over the private Compose network. Reload Caddy after editing its mounted configuration in place; when deployment replaces `Caddyfile`, recreate `remnawave-proxy` with `docker compose --env-file .env -f docker-compose.yml up -d --no-build --no-deps --force-recreate remnawave-proxy` so its file bind mount reads the replacement. The public probe is `https://<CONNECT_DOMAIN>/platega/healthz`. Authenticated callbacks are durably queued in SQLite and acknowledged immediately. A background worker checks the authenticated transaction-status API, verifies the payment ID, amount, currency and any supplied payload, then credits or fulfills once. Callback payload is optional, as documented. A missing or delayed callback is recovered by automatic polling every 10 seconds for recent invoices, with slower checks for older invoices and backoff after API errors. Users receive confirmation automatically, and the payment message is updated; manual checking is not part of the new payment keyboard.

Caddy overwrites `X-Real-IP` with the connecting client address. The bot accepts that header only from `PLATEGA_TRUSTED_PROXY_IP`, which Compose sets to Caddy's reserved backend address `172.30.50.11`; other peers are identified by their socket address. Valid IPv4 and IPv6 addresses are normalized and converted to a source identifier with HMAC-SHA256 using the bot token. Rejection logs contain that identifier and the exception class, without raw addresses, request bodies, credentials or exception text. Rotating the bot token changes source identifiers. Applying this local change requires updating Compose and recreating both Caddy and the bot; a Caddy reload alone does not assign the reserved address. The standalone bot leaves proxy trust disabled by default.

The API may return the service quote in `paymentDetails.amount` with a separate commission, as observed for pending crypto invoices, or a total including the payer fee. Reconciliation compares the immutable service quote with the API amount and, for `comissionType: 1`, its amount less the validated `comission`. Callback amounts must agree with the API amount using the same payer-fee metadata. Wallet credits, bonuses, refunds, subscription fulfillment and the confirmation screen use the stored service quote, while the payment form shows the final total with any payer fee.

Payment checks and the notification outbox survive restarts in the shared SQLite volume. Telegram delivery errors retry independently of financial processing. Interrupted orders already marked paid can resume fulfillment; interrupted or uncertain Panel mutations go to the existing review workflow to avoid a duplicate extension. Admins can filter online transactions by status, check provider status, export a CSV, and confirm a refund request. The refund ledger waits for `CHARGEBACKED` verified through the API; callbacks and automatic polling both reconcile refunds. Repeated or out-of-order notifications cannot re-credit a refunded payment.

This product currently sells prepaid terms; it does not silently enable automatic recurring renewals. Platega recurring subscriptions need separate opt-in, renewal/cancellation policy, and subscription-charge reconciliation. Payout API calls also require a dedicated payout secret and idempotency-key persistence; do not substitute `PLATEGA_SECRET` or enable payouts without those credentials and an explicitly approved payout workflow. A real 1 ₽ top-up was credited on October 24; refunds remain unverified.

## Subscription Page

`remnawave-subscription-page` is a separate container (`remnawave/subscription-page:8.0.0`, digest pinned in Compose) on the `backend` network with no published port. Caddy serves it at the root of `connect.example.com`, so the page URL is `https://connect.example.com/<short_uuid>`. It cannot live under a path because its frontend assets are requested from `/assets`. The page host must be a host root; do not attach it to the Panel SPA host or share a path with it.

Caddy serves HTTP/1.1 and HTTP/2 and sends `Alt-Svc: clear` on the Panel and connection hosts. UDP `443` belongs to Hysteria 2; its default HTTP fallback returns 404 when a browser follows a previously advertised HTTP/3 route. The Hysteria 2 inbound proxies those browser requests to the HTTPS connection host, allowing the page to load and the browser to clear its saved alternative route. Verify the rendered page and `/assets/.app-config-v2.json` in a normal browser with QUIC enabled, including a browser that opened the page before the change, since an HTTP-only command-line probe does not detect this conflict.

Required environment values:

- `SUBPAGE_CONFIG_UUID=00000000-0000-0000-0000-000000000000` selects the reserved page configuration. The Panel can also return a config UUID from an external squad or subscription response rule; when none is returned, the container falls back to this value.
- `SUBSCRIPTION_PAGE_URL=https://connect.example.com` enables the bot's “🌐 Страница подписки” button. The bot derives `<origin>/<short_uuid>` from the user's own subscription URL and only shows the button in a private chat. Leave it empty to hide the button.
- `REMNAWAVE_PANEL_URL=http://remnawave:3000` and `TRUST_PROXY=1` are fixed in Compose for the internal network and Caddy.

Page configuration (title, meta, logo, support link, locales, app guides, connection keys, subscription info) lives in Panel under Subscription Page configs and is managed through the Panel UI or API. Subscription Settings (custom remarks, response headers, update interval, HWID, JSON-at-base) are separate and affect the actual subscription response. Get the page publicly reachable and its configuration loaded before giving the URL to users:

```bash
docker compose --env-file .env -f docker-compose.yml up -d remnawave-subscription-page
docker compose --env-file .env -f docker-compose.yml logs --tail=50 remnawave-subscription-page
curl --silent --show-error -o /dev/null -w '%{http_code} %{redirect_url}\n' -H 'Accept: text/html' https://connect.example.com/
```

The host root has no page content, so Caddy redirects `/` to the Telegram channel. The page itself is only at `/<short_uuid>`. It returns HTML only for browser-like `Accept` and `User-Agent` headers; other requests fall back to the subscription representation, so a bare `curl` may not represent a browser. Open a user's own page URL in a real browser to verify. Check that the log shows the Panel version and loaded config, and that the certificate for the `connect` host exists.

Rollback: stop and remove `remnawave-subscription-page`, remove the `{$CONNECT_DOMAIN}` site from `Caddyfile`, and reload Caddy. Panel subscriptions at `remnawave.example.com/api/sub` are unaffected; the raw VLESS inbound on `2053/TCP` is not routed through Caddy.

## Profile and client path

The user flow is Telegram `/start` → the user either activates the free 3-day trial after subscribing to the channel, or an admin grants access through the Panel API → Panel creates the user in the Internal Squad → user receives their own Panel subscription in a private chat → Happ imports the HTTPS subscription → client connects directly to `connect.example.com:2053` → traffic exits through the Germany Node. The bot never builds VPN links itself.

Open the HTTPS Subscription Page from the bot and follow its Happ import instructions. Do not copy a subscription URL into group chats or logs. The Panel Host determines the advertised public VPN address; `PANEL_API_URL` only identifies the management API and must not be used as the VPN endpoint. A previous production report recorded a successful temporary-user tunnel test through public TCP `2053`, with traffic exiting via the Germany VPS; the current bot-only rollout did not repeat it. Happ import on a real device and whitelist-condition testing remain unverified.

The profile uses VLESS + REALITY over `raw`/TCP with no Vision flow. Select REALITY target, server name, key, and short ID using the pinned Panel/Node workflow and verify the generated subscription in Happ. A valid DNS record and successful `xray run -test` do not prove external network access or whitelist bypass.

## Add Hysteria 2 as a third connection

The pinned Node runs Xray `26.7.28`. Remnawave recognizes Hysteria 2 as the `hysteria` inbound protocol with version `2`, and derives each user's Hysteria credential from that user's managed UUID. Happ accepts Hysteria 2 links. This is an additional transport; it does not replace VLESS + REALITY or XHTTP.

Use the Panel administrator UI or a maintenance API token authorized to update Config Profiles to add `hysteria2-inbound.json` to the existing `Germany Exit` Config Profile. Activate `JOPES_HYSTERIA2_IN` on `Germany-1`, create a Host for that inbound at `connect.arzhelper.tech:443`, and add the inbound to the existing `JOPES Direct` Internal Squad. Preserve the existing inbounds and settings. The Host must use TLS/SNI `connect.arzhelper.tech`. This grants Hysteria 2 to every user already in that squad.

Set `streamSettings.hysteriaSettings.masquerade.url` to `https://<CONNECT_DOMAIN>` in the inbound template. The proxy uses Caddy over TCP with certificate verification and preserves the request path, so browsers with a saved HTTP/3 route can still load the subscription page while authenticated Hysteria 2 connections continue using the same listener.

The Node reads a dedicated copy of Caddy's certificate, not Caddy's private-data volume. Copy the deployment files to `/opt/jopesvpn/deploy/production`, then run as the Docker-capable account:

```bash
cd /opt/jopesvpn/deploy/production
docker compose --env-file .env -f docker-compose.yml -f docker-compose.node.yml --profile node up -d --no-deps remnawave-node
docker volume create jopesvpn-production-hysteria2-certs >/dev/null
sudo install -m 0755 sync-hysteria2-cert.sh /usr/local/sbin/jopesvpn-sync-hysteria2-cert
sudo install -m 0644 jopesvpn-hysteria2-cert-sync.service /etc/systemd/system/
sudo install -m 0644 jopesvpn-hysteria2-cert-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now jopesvpn-hysteria2-cert-sync.timer
sudo systemctl start jopesvpn-hysteria2-cert-sync.service
sudo systemctl --no-pager --full status jopesvpn-hysteria2-cert-sync.service
```

The initial certificate sync restarts the Node once; later syncs restart it only when Caddy renews the certificate. Opening a firewall port requires root, and provider firewalls are separate:

```bash
sudo ufw allow 443/udp comment 'JopesVPN Hysteria 2'
sudo ufw status numbered
```

Also allow UDP `443` in the VPS provider firewall. Verify `docker inspect jopesvpn-production-node`, Panel Node connectivity, UDP `443`, and the user's own subscription in Happ before announcing availability. If the Panel editor or generated subscription on version `3.4.5` rejects this inbound, leave it disabled; do not create a shared static password as a workaround.

## Health checks and troubleshooting

Never inspect environment values or dump full Panel subscriptions/configs into logs. Use:

```bash
docker compose --env-file .env -f docker-compose.yml ps
docker compose --env-file .env -f docker-compose.yml logs --tail=100 remnawave remnawave-subscription-page remnawave-proxy remnawave-db remnawave-redis
curl --fail --silent --show-error https://remnawave.example.com/health
curl --silent --show-error -o /dev/null -w '%{http_code} %{redirect_url}\n' -H 'Accept: text/html' https://connect.example.com/
getent ahostsv4 remnawave.example.com
getent ahostsv4 connect.example.com
nc -vz connect.example.com 2053
```

Check Node status in Panel. From another network, verify `2222/TCP` is closed and `2053/TCP` is reachable. `nc` only checks TCP reachability, not the REALITY handshake. Check bot process health with `docker compose ... ps` and sanitized application logs; do not enable HTTP request/header logging for Panel API calls.

For bot-only logs:

```bash
docker compose --env-file .env -f docker-compose.yml --profile bot logs --tail=100 bot
```

## Update, stop, backup, and recovery

- Keep all image tags and digests pinned. Before an update, review official Remnawave release notes and supported Panel/Node pairing, take and verify backups, then update the selected digest/tag intentionally. Do not use `latest`.
- **Tariff UX update (production, 2026-10-18):** the single sellable unlimited plan now offers 7, 15, 30, 90, and 180 days; prior unlimited codes remain archived. Traffic plans are selected by reset-period family. Term buttons show the full RUB quote and any discount; the payment screen only repeats the plan and term, while method prices stay on buttons. The Stars-to-RUB equivalent is not displayed. Periodic quota messaging follows the Panel calendar schedule, not subscription stages. Before rollout, changed-source SHA-256 values were matched, the current bot-data volume and source files were backed up, and SQLite `integrity_check` returned `ok`. Rollback images: `jopesvpn-bot:rollback-tariff-ux-20261018T153802Z` and `jopesvpn-ticket-portal:rollback-tariff-ux-20261018T153802Z`. Only the bot image was rebuilt; bot and ticket-forum were restarted. `.env`, payment settings, Panel, Node, Caddy, Subscription Page, database schema, and VPN listeners were not changed. Afterward, Telegram `getMe` reported 39 handlers; the running catalog loaded 22 sellable plans with 32 unique tags and one sellable unlimited plan; SQLite integrity and Panel health passed; both app containers were running with restart count 0; support redirected with HTTP 303, `2053/TCP` remained listening, and no critical/error/traceback markers appeared in the checked 10-minute bot log window. Focused local tests passed (38); `compileall` passed; Ruff was unavailable locally. No real Stars payment or user subscription mutation was performed.
- **Tariff catalog and payment-choice rollout (production, 2026-10-18):** expanded the catalog to 24 active tariff codes with unique Panel tags; the checkout asks for a payment method after term selection. Telegram Stars is the only enabled method. SBP, card, and crypto only show the fixed RUB quote and technical-work notice; selecting them creates no order. Before the bot restart, both SQLite writers were stopped, the shared bot-data volume was copied and its backup passed `PRAGMA integrity_check`, and `jopesvpn-bot:rollback-method-picker-20261018T151245Z` was retained. Only the bot image was rebuilt; bot and ticket-forum were started, while Panel, Node, Caddy, PostgreSQL, Valkey, `.env`, and VPN listener configuration were left unchanged. The Russian catalog was validated as UTF-8 in both the deployed image and running container. Post-deploy checks: Telegram API, 24 active plans/unique tags, SQLite integrity, Panel HTTP 200, bot and ticket-forum running with restart count 0, zero filtered bot error markers, and `2053/TCP` open. No Stars payment or existing-user mutation was performed.
- **Tariff checkout rollout (production, 2026-10-18):** before the additive `orders` migration, the shared `/app/data` directory was copied while both SQLite writers were stopped; backup `tariff-predeploy-20261018T144531Z` passed `PRAGMA integrity_check`. Rollback images were saved for bot and ticket portal. Only bot was rebuilt; bot and ticket-forum were restarted. Panel, Node, Caddy, PostgreSQL, Valkey and VPN configuration were not changed. Telegram `getMe` (39 handlers), 8 active tariffs/unique TAGs, order schema, SQLite integrity, Panel health, `2053/TCP` and container states were checked; both app services run with restart count 0. An invalid-ID update probe reached Panel validation without modifying a user. No Stars transaction, subscription mutation, or quota-exhaustion tunnel test was performed.
- **Ticket portal rollout (2026-10-18):** before rollout the existing bot image was tagged for rollback and the complete SQLite data directory was copied while the bot was stopped; `PRAGMA integrity_check` returned `ok`. New bot and portal images were built locally on the VPS, then only `bot`, `ticket-forum`, and Caddy were updated. The Caddy container was recreated to attach its new private network; this briefly interrupted HTTPS/Panel web access, but Panel, Node, database, Subscription Page, and the VPN inbound were not restarted or changed. Public `/support/` and `/support/admin` returned HTTP 200, the connect host root retained its HTTP 302 behavior, Telegram `getMe` succeeded, and the portal has no published host port. An automated admin login/session smoke-check passed without displaying the generated password; no real ticket or user reply was created.
- **Current bot and ticket portal rollout (2026-10-18):** rollback images for both services and a protected copy of the shared SQLite data were saved before rollout; backup integrity passed. Only bot and ticket-forum were rebuilt/recreated. Panel, Node, Caddy, PostgreSQL, DNS and the VPN inbound were not restarted. Telegram `getMe`, required commands, Panel `nodes:list`, SQLite integrity/migrations, admin login/session, unauthenticated admin denial and user-ticket 404 were verified. No real Stars payment or ticket was created; `2053/TCP` remained listening.
- **Channel post editor rollout (2026-10-19):** backed up the shared SQLite directory and retained rollback images `jopesvpn-bot:rollback-20261019T192817Z` and `jopesvpn-ticket-portal:rollback-20261019T192817Z`; backup integrity passed. Only bot and ticket-forum were rebuilt/recreated. The additive `channel_posts` and `channel_post_clicks` tables were created. Telegram `getMe` and 53 registered handlers passed; bot and portal run with restart count 0, SQLite integrity is `ok`, and unauthenticated `/support/admin/posts` returns 401. No channel post was published during verification.
- **Previous admin cards, Panel-data resilience, trial resources, and callback hotfix (2026-10-18):** before each bot/ticket-forum stop, rollback images and protected SQLite copies were retained; both backup integrity checks passed. Only the bot image was rebuilt; bot and ticket-forum restarted. Telegram `getMe` reported 33 handlers; polling process and both services are running with restart count 0. Panel health returned HTTP 200, SQLite integrity returned `ok`, and `2053/TCP` remained listening. At that point traffic plans were not yet deployed; see the newer tariff rollout above.
- **Bot-only update after UX/referral changes (historical):** the 2026-10-17 rollout used the earlier procedure. For future bot+portal releases, back up SQLite state and restart only bot and ticket-forum, so polling and portal access pause briefly; VPN, Panel, Node, Caddy and their ports are not intentionally changed. Run as the Docker-capable SSH account; store the private backup under that account's home directory, not a web-served path. Preserve both old images before stopping both writers, then copy the complete data directory from the stopped bot container:

  ```bash
  BACKUP_SUFFIX="$(date -u +%Y%m%dT%H%M%SZ)"
  ROLLBACK_TAG="jopesvpn-bot:rollback-${BACKUP_SUFFIX}"
  PORTAL_ROLLBACK_TAG="jopesvpn-ticket-portal:rollback-${BACKUP_SUFFIX}"
  BOT_DATA_BACKUP="$HOME/jopesvpn-backups/bot-data-pre-update-${BACKUP_SUFFIX}"
  install -d -m 700 "$HOME/jopesvpn-backups"
  docker image tag jopesvpn-bot:1.0.0 "$ROLLBACK_TAG"
  docker image tag jopesvpn-ticket-portal:1.0.0 "$PORTAL_ROLLBACK_TAG"
  docker compose --env-file .env -f docker-compose.yml --profile bot stop bot ticket-forum
  install -d -m 700 "$BOT_DATA_BACKUP"
  docker cp jopesvpn-production-bot:/app/data/. "$BOT_DATA_BACKUP/"
  chmod -R go-rwx "$BOT_DATA_BACKUP"
  python3 -c 'import sqlite3,sys; db=sqlite3.connect("file:"+sys.argv[1]+"?mode=ro",uri=True); result=db.execute("PRAGMA integrity_check").fetchone()[0]; print(result); sys.exit(result!="ok")' "$BOT_DATA_BACKUP/bot-state.sqlite3"
  ```

  Then rebuild/recreate only bot and ticket-forum. Do not dump unfiltered production logs or trigger trial/referral rewards as smoke tests against real users; cover those flows with tests or a controlled test account:

  ```bash
  docker compose --env-file .env -f docker-compose.yml --profile bot up -d --build --no-deps bot ticket-forum
  docker inspect --format '{{.State.Status}} restart_count={{.RestartCount}}' jopesvpn-production-bot
  docker exec jopesvpn-production-bot python -m jopesvpn.bot --check-telegram
  ```

  SQLite migrations are additive: they add navigation/order metadata, support ticket tables/columns, wallet payment/bonus audit tables, payment-method/expiry columns, `platega_checks`, and `payment_outbox` without deleting Panel accounts or VPN data. Bot startup backfills confirmed Stars top-ups, paid subscription orders, and settled withdrawals into payment history. Admins use **Balances and bonuses** for reason-required balance adjustments, bonus configuration, and payment lookup; **Online payments** adds status filters, provider status checks, CSV export, and confirmed refund requests. Make a protected SQLite backup before rollout, then preserve the migrated database during application rollback. If rollback is needed in the same shell, retag both saved pre-update images:

  ```bash
  docker compose --env-file .env -f docker-compose.yml --profile bot stop bot ticket-forum
  docker image tag "$ROLLBACK_TAG" jopesvpn-bot:1.0.0
  docker image tag "$PORTAL_ROLLBACK_TAG" jopesvpn-ticket-portal:1.0.0
  docker compose --env-file .env -f docker-compose.yml --profile bot up -d --no-build --no-deps bot ticket-forum
  ```

  Keep the migrated SQLite volume: after referral activity, restoring its pre-update copy could erase reward reservations and allow a duplicate extension. Do not retry a reward left `pending` without manually checking the inviter's current Panel expiry.

- General stack update without deleting volumes:

  ```bash
  docker compose --env-file .env -f docker-compose.yml pull
  docker compose --env-file .env -f docker-compose.yml --profile bot up -d --build
  docker compose --env-file .env -f docker-compose.yml -f docker-compose.node.yml --profile node up -d
  ```

  Review output before upgrading Panel and Node together; restore from a verified backup if migration/health checks fail.
- Stop safely (state remains in named volumes):

  ```bash
  docker compose --env-file .env -f docker-compose.yml --profile bot stop
  docker compose --env-file .env -f docker-compose.yml -f docker-compose.node.yml --profile node stop
  ```

- Back up PostgreSQL and Caddy certificate/account data to encrypted storage outside the VPS. The bot's `jopesvpn-production-bot-data` volume contains user preferences, notification bookkeeping, banner IDs, connection-log state, onboarding markers, and referral attribution/reward state; losing it does not remove VPN accounts but does lose bot history and referral idempotency state. A database dump example (create a protected backup directory first):

  ```bash
  install -d -m 700 /root/jopesvpn-backups
  umask 077
  docker compose --env-file .env -f docker-compose.yml exec -T remnawave-db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > /root/jopesvpn-backups/panel-$(date -u +%Y%m%dT%H%M%SZ).sql
  ```

  Back up Caddy's certificate/account data as well. This briefly stops HTTPS while the volume is copied:

  ```bash
  docker compose --env-file .env -f docker-compose.yml stop remnawave-proxy
  docker cp jopesvpn-production-proxy:/data /root/jopesvpn-backups/caddy-data
  docker compose --env-file .env -f docker-compose.yml start remnawave-proxy
  ```

  Never back up into a web-served directory. Test restore to a separate instance or new VPS using a separate volume; do not overwrite the original production volume during a recovery attempt. A full encrypted off-host backup and a PostgreSQL restore into an isolated temporary database were verified on 2026-10-25; see the Windows procedure below.
- All Compose services use the `json-file` log driver with `max-size: 10m` and `max-file: 3`.
- For reboot recovery, Docker's `unless-stopped` policy restarts the services and their named volumes persist. Verify `docker compose ps`, Panel health, Node status, certificate, and the external VPN check after reboot.
- Never use `docker compose down -v` in routine updates or troubleshooting; it removes persistent state.

## Full off-host recovery backup and monitoring

Run `offhost-backup.ps1 -FullBackup` from the trusted Windows workstation using the existing SSH alias. It exports a consistent SQLite online backup, a PostgreSQL custom dump, Caddy certificate/account data and deployment configuration, including both private environment files. No production service needs to stop. The archive is encrypted with AES-256-CBC/PBKDF2 and authenticated with HMAC-SHA256 before verification. Encrypted snapshots and the DPAPI-protected key are stored under `%USERPROFILE%\JopesVPN-Backups`, with access restricted to the current account. OpenSSL can be supplied with `-OpenSslPath`; the script also checks the Git for Windows installation.

Verify a full backup and an isolated PostgreSQL restore, configure monitoring, then install the daily backup and five-minute monitoring tasks. Run these commands from `deploy/production`:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\offhost-backup.ps1 -FullBackup -VerifyPostgresRestore
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\monitor.ps1 -Setup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install-operations-tasks.ps1
Start-ScheduledTask -TaskName JopesVPN-OffhostBackup
Start-ScheduledTask -TaskName JopesVPN-Monitor
Get-ScheduledTaskInfo -TaskName JopesVPN-OffhostBackup
Get-ScheduledTaskInfo -TaskName JopesVPN-Monitor
```

`-VerifyPostgresRestore` restores the dump into a separate temporary database, checks that it has tables, then removes that database. It does not restore over the working Panel database. Every backup verifies archive hashes, SQLite integrity and archive paths; old SQLite-only snapshots remain supported. Keep the Windows user profile and its DPAPI key in protected workstation backups. The workstation must be on and the user logged in for these scheduled tasks; they are not a replacement for an always-on monitoring host. Review `backup.log`, `%USERPROFILE%\JopesVPN-Monitor\monitor.log` and Task Scheduler results.

For a manual recovery rehearsal, restore only to a new protected path; the script refuses to overwrite files. A decrypted full archive contains secrets and must be removed after verification:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\offhost-backup.ps1 -RestoreFile "$env:USERPROFILE\JopesVPN-Backups\recovery-<timestamp>.tar.gz.enc" -RestoreTo "$env:USERPROFILE\JopesVPN-Backups\restore-check.tar.gz"
```

The monitor checks HTTPS bot readiness, Panel health, support authentication, all eight container states, available RAM, swap and the two VPN TCP listeners over SSH. Defaults require two consecutive failures/recoveries, at least 500 MiB available RAM and at most 75% swap use. Configure thresholds with `monitor.ps1 -Setup` arguments. Its configuration is protected with DPAPI; logs contain only status and aggregate resource measurements. Telegram notifications are disabled by default. Enable them only when the owner authorizes sending to `BOT_ADMIN_IDS`, using `monitor.ps1 -Setup -EnableTelegramAlerts`.

The bot exposes `/healthz` on its existing internal port `8081`; Caddy publishes that route. Docker readiness depends on completed startup and a successful read-only Telegram `getMe` within `HEALTH_PROBE_MAX_AGE_SECONDS`. Probe frequency is `HEALTH_PROBE_INTERVAL_SECONDS`. Failures are deduplicated in logs; the route exposes status and uptime only.

## Domain templates

Owner operations are configured with `BOT_OWNER_ID` and `OPS_*` in `.env.example`.
Run `python3 install-user-operations.py` as the existing deployment user to install
the five-minute monitor and expired-test cleanup without sudo. Only that owner
receives operational notifications. Critical failures/recoveries are deduplicated;
noncritical signals appear in a daily summary at 07:00 UTC. Tickets waiting more
than two hours receive one reminder per latest unanswered message.

The external monitor runs in the public source repository's default-branch
`service-monitor` workflow. Encrypted recovery artifacts and the runbook are in a
separate private repository. Setup uses dedicated SSH keys with forced allowlists,
never the personal deployment key. See [RECOVERY.md](RECOVERY.md) for verification,
retention and the remaining full-host disaster rehearsal. GitHub cron can be delayed.
Keep Windows monitoring passive once these checks are enabled to avoid duplicate alerts.

`/service`, `/privacy_process` and `/payment_check_status` are private owner commands.
`/delete_data` creates an idempotent request; it does not cancel VPN access or erase
financial records automatically. The `/payment_check` owner command prepares or
renews expired control invoices; automatic refunds are restricted to this flow.
Legal drafts must be reviewed before setting document URLs in the existing admin menu.

Set `APEX_DOMAIN`, `PANEL_DOMAIN`, `CONNECT_DOMAIN` and `TRIAL_CHANNEL_USERNAME` in the existing production environment file. Caddy uses those variables directly. Validate Compose with `config --quiet`; avoid printing expanded configuration because it contains secrets.

The Hysteria inbound is a template. Render to a separate, ignored output before a future profile update:

```bash
python3 render-config.py --env-file .env --output hysteria2-inbound.rendered.json
```

Rendering validates the configured DNS host and refuses to overwrite either input. It does not update any live Panel profile. The certificate helper reads `CONNECT_DOMAIN` from `ENV_FILE`, with optional `CADDY_DATA_DIR` and `HYSTERIA2_CERT_DIR` overrides. Installing a changed helper under `/usr/local/sbin` needs the owner's sudo access; validate the script with `sh -n` before installation. Preserve the working certificate timer until that installation is complete.

## Validated vs pending

Locally, the project tested Panel `3.4.5` with Node `3.4.2` and bundled Xray `26.7.28`, a Panel subscription, HTTPS over a same-host bridge→exit tunnel, exit outage, and recovery. This Compose stack is deployed on the Germany VPS; the Panel, Node, bot, Caddy, and subscription page containers were verified there, and the raw and XHTTP subscriptions returned HTTP 200 through a test tunnel from the VPS. A previous production report also recorded a temporary-user tunnel over public TCP `2053` with Germany VPS egress. The 2026-10-17 bot-only ticket release verified the bot container, Telegram `getMe`, ticket tables, and SQLite integrity, without changing Panel/Node/VPN services. Full off-host backup and an isolated PostgreSQL restore were verified on 2026-10-25. Happ import on a real device, current external-client behavior, full VPS recovery and whitelist conditions remain unverified. Verify every item again after any redeploy to a new VPS.

Official installation references checked 2026-10-17:

- [Remnawave requirements](https://docs.rw/install/requirements/)
- [Remnawave environment variables](https://docs.rw/install/environment-variables/)
- [Remnawave Caddy reverse proxy](https://docs.rw/install/reverse-proxies/caddy/)
- [Official Remnawave Node production Compose (3.4.2)](https://github.com/remnawave/node/blob/3.4.2/docker-compose-prod.yml)
- [Project's verified local Panel/Node pair and image digests](../../config/remnawave-lab/README.md)
