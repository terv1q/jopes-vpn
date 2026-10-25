# Jopes VPN: recovery independent of the workstation

The encrypted snapshots and this runbook are also stored in the private
`jopesvpn-recovery` repository of the same GitHub owner as the source repository.
The Windows DPAPI profile is not required for the cloud restore workflow:
the recovery key is independently stored as a GitHub Actions secret.
Never print that secret or download a decrypted archive as an Actions artifact.

## Schedule and verification

Cloud export is scheduled twice an hour. Hourly copies remain for two days,
daily copies for 14 days, weekly copies for eight weeks. Only encrypted files
and their HMACs are uploaded. Every export is decrypted in the ephemeral runner
and checked for archive integrity, SQLite integrity and PostgreSQL dump format.
The weekly verification restores PostgreSQL into a separate temporary database.
The working database is never overwritten during a verification run.

The production monitor alerts the owner when no successful off-host upload has
been acknowledged within 75 minutes or a backup run fails. GitHub schedules can
be delayed; the one-hour RPO is a target monitored by these alarms, not a hosted
SLA. A stricter guarantee requires a paid scheduler and independent object storage.

To verify recoverability after losing both VPS and workstation, sign into GitHub
from another computer, open this private repository, choose the newest successful
`encrypted-recovery` run, and note its encrypted artifact ID. Run
`verify-stored-recovery` with that ID. It downloads the archived copy, checks HMAC,
decrypts using the independently stored secret and validates the recovered files.
It also restores the stored PostgreSQL dump into a fresh network-isolated PostgreSQL
container on the GitHub runner and checks that tables exist. Its logs show only the
verification result. This procedure does not contact the VPS.

## Restore to a replacement VPS

Budget 1–2 hours after the replacement VPS and account/DNS access are available.
The bot source commit is recorded in the archive's `config/RELEASE_COMMIT`;
the tariff catalog is in `config/plans.json`. Obtain that exact source revision,
not whichever branch happens to be newest. The archive includes:

- SQLite bot state: payments, wallet, referral reservations, tickets, vouchers.
- PostgreSQL custom dump with Panel configuration and VPN users.
- Caddy certificate/account data and the private deployment environment files.
- Compose, Caddy, Hysteria template/helper, the live tariff catalog and source revision.

Restore into fresh volumes on the replacement host. Keep recovered files in a
root-only directory with `umask 077`; never serve it over HTTP. Restore the Panel
custom dump with `pg_restore --no-owner --single-transaction`, restore the bot SQLite
and its ownership to UID 10001, restore the Caddy data archive with a safe tar filter,
then install the exact recorded application source and recovered configuration.
Existing source documentation describes the Compose/Node commands and certificate
helper installer. Change DNS only after verifying the replacement host and its TLS.

Required validation before accepting traffic: Panel and bot health, SQLite integrity,
one polling process, Node connection, TCP 2053/8443, UDP 443, test subscription import,
and reconciliation of provider payments received during the outage. Do not reissue
wallet/referral rewards blindly. Preserve the restored SQLite during code rollback.

The isolated archive/PG restore checks validate backup contents. Rebuilding a complete
replacement VPS and client connectivity is a separate disaster rehearsal; it has not
been performed without a second server. Owner authentication to GitHub and the hosting/
DNS accounts remains necessary in an actual disaster. Enable two-factor authentication
and retain recovery codes independently of the workstation and VPN server.

## Optional independent object storage

The default uses private GitHub encrypted artifacts to avoid a new paid account.
For growth, prefer encrypted restic backups to object storage such as Backblaze B2;
check current storage, region, egress and tax terms before buying.
Keep the encryption key in a password manager independent of the VPS and PC.
Use a managed external uptime check if a contractual alert interval is required.
