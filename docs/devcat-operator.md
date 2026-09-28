# DevCat Yandex MCP operator guide

Target: BruceVPS, single account, fork commit pinned in `/opt/yacli-devcat/releases/<commit>`. The service example is in `ops/devcat-yandex/`. The public connector must use the existing managed Secure MCP Tunnel; the Rust service binds only to `127.0.0.1:8787` and requires a separate MCP bearer token even on loopback. Do not point a public reverse proxy at the listener.

## Prepare

1. Register a DevCat-owned Yandex OAuth application with confirmation-code + PKCE support. For the initial read-only deployment, yacli requests Mail `mail:imap_ro` and Disk `cloud_api:disk.info` plus `cloud_api:disk.read`; enabling send adds `mail:smtp`, while enabling Disk mutation adds `cloud_api:disk.write`. The registered redirect URI must match yacli's verification-code URI. Record only its client ID in the systemd unit. Never use the compiled-in upstream client ID for production.
2. Through Bruce Secrets Panel, provision exact key `DEVCAT_YANDEX_MCP_BEARER`. For OAuth, run `yacli login mail` and `yacli login disk` separately under the DevCat environment (`YACLI_DEVCAT_MODE=1`, configured account, roots, owned client ID, and `YACLI_CONFIG_DIR`). The owner opens the generated Yandex authorization URL and enters its one-time code directly in the terminal. The CLI exchanges the code and writes each token directly to the exact Bruce vault key named in `accounts.toml` through `bruce-secret put/replace`; it does not create `credentials.toml` or print token values. Do not paste values into chat, repository files, CLI arguments, service environment, or logs. The BruceVPS `codex` service identity must be able to run the approved exact-key `bruce-secret` helper; check presence by exit status, never by printing a token. Yandex app registration and consent remain owner-only actions.
3. Copy `accounts.toml.example` to `/var/lib/yacli-devcat/config/accounts.toml`, substitute the email, and set mode `0600`. It stores only vault key names. Do not install `credentials.toml` or a production `.env`. Keep Mail and Disk OAuth grants separate so read-only scopes can be used where Yandex permits them.
4. Build `cargo build --locked --release` from this fork's reviewed commit in the supported CI/build environment. Install the resulting binary and the MIT `LICENSE` under `/opt/yacli-devcat/releases/<commit>`, with `current` symlink pointing to the release. Fill in the owned client ID in the unit, install it to `/etc/systemd/system/yacli-devcat.service`, then `systemctl daemon-reload && systemctl enable --now yacli-devcat`.
5. Probe `curl --fail http://127.0.0.1:8787/healthz` and verify the listener with `ss -lnt`. An unauthenticated `POST /mcp` must return 401. Run the managed Secure MCP Tunnel's established auth/health checks before registering the ChatGPT connector. The tunnel should forward the MCP bearer token from its approved secret-aware configuration; it must not log it. If the tunnel forwards a browser `Origin`, set `YACLI_DEVCAT_ALLOWED_ORIGINS` to the exact trusted origin; never use a wildcard.

## Capabilities and review

`YACLI_DEVCAT_CAPABILITIES` defaults to `mail.read,disk.read`. Add `mail.send`, `disk.write`, or `disk.publish` only after the corresponding operator review. `mail.mutate`, `mail.delete`, and `disk.delete` are independently reserved and grant no tool in this release. No Mail/Disk delete MCP tool is registered. Disk paths must be explicit and inside `YACLI_DEVCAT_DISK_ROOTS`; percent escapes, alternate separators, dot segments, Unicode compatibility normalization, and empty segments are rejected. Local upload and Mail attachment sources require `YACLI_DEVCAT_UPLOAD_ROOTS` and must resolve to a regular file under a configured local root.

After adding a capability, repeat the matching OAuth login so the Yandex grant includes the newly needed scope. A capability flag alone never expands an existing token's Yandex permissions.

Risky tools require two calls: first `dry_run=true`; inspect the review output and retain `review_sha256`; then repeat the identical arguments with `dry_run=false` and the returned `review_sha256`. The digest also binds local source file bytes, expires after five minutes, and is consumed once. The service emits content-free JSON mutation audit records to the systemd journal (`journalctl -u yacli-devcat`). The general yacli activity store is disabled in DevCat mode. Restrict journal access to operators.

`yacli.disk.read` returns at most 1 MiB as base64; larger files need a separately reviewed transfer workflow. Mail read/search and Disk list/read do not require mutation capabilities. Calendar, dashboard, activity undo, prompts, resources, and other broad upstream MCP surfaces are disabled by the DevCat profile.

## Update and rollback

Build and test the next pinned fork commit, install a new release directory, save the old `current` target, switch the symlink, and restart. Re-run loopback health, 401, authenticated tool list, read-only Mail search/read, and Disk list/read. For rollback, restore the old `current` target and restart. Keep the old binary and service unit until the new read canary passes. Do not use upstream `yacli update` on this service.

## Live canary boundary

After OAuth consent and vault presence, use a dedicated test mailbox message and a dedicated file under the allowed Disk root to prove Mail search/read and Disk list/read. Only if mutation capabilities are intentionally enabled, create a new test object inside that root and use dry-run + review digest before publish/send/unpublish. Never mutate existing user data as a canary. If credentials are absent, report exactly the missing app registration/consent/vault-key gate and leave the service unexposed.
