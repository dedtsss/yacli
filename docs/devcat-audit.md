# DevCat Yandex MCP: bounded audit and reuse decision

Issue: dedtsss/agent-dispatch#551. Baseline: NextStat/yacli 0.5.5, commit `7a44002`, MIT.

## Decision

Use the user-owned `dedtsss/yacli` fork. Its Rust implementation already has Mail IMAP/SMTP XOAUTH2, Disk REST, account selection, PKCE, stdio and HTTP MCP, dry runs for several operations, and extensive tests. A separate server would duplicate the protocol and provider code. Keep the upstream CLI usable; add a restrictive DevCat MCP runtime profile at the MCP boundary.

## Code audit

| Area | Baseline evidence | Required boundary |
| --- | --- | --- |
| OAuth | `src/oauth.rs` uses PKCE and service scopes; `src/commands.rs` defaults to a compiled-in upstream client ID | Require configured DevCat client ID for the service profile; avoid the upstream ID in production |
| Token storage | `src/credential_store.rs` defaults to a local credentials file on Linux when no keyring is selected | Service uses the configured exact-key helper (`YACLI_DEVCAT_VAULT_HELPER`); CatVPS points it at `/usr/local/libexec/catvps-vault`, which reaches canonical Bruce Secrets without a second store or local fallback |
| Mail | `src/mail.rs` uses `BODY.PEEK` for fetch; MCP exposes search, read, send, reply, forward, attachment export, and invite to calendar. There is no Mail delete MCP tool | Independent read/send/mutate/delete gates; no send from read tools |
| Disk | `src/mcp/server.rs` exposes list, mkdir, upload, upload-link, download, publish, unpublish; no delete MCP tool | Separate read/write/delete/publish gates and root allowlist; upload-link needs both write and publish |
| HTTP MCP | `src/mcp/server.rs` authenticates selected `tools/call`, while metadata, resources, app snapshot and some other methods may expose account data; listen address is configurable | Authenticate all sensitive methods and bind the production service to loopback for Secure MCP Tunnel |
| Activity | `src/activity_store.rs` records replay commands and summaries that may contain mail contents, paths or published URLs | Add a minimal fixed-field mutation audit log without request/response content |
| Tests/update | `tests/mcp_http.rs`, `tests/mcp_server.rs`, CI and Cargo.lock exist; self-update follows upstream release | Add boundary tests; pin deployment to fork commit with rollback path |

The read-only MCP surface still needs careful classification: dashboard/goal/suggestion tools can derive private data or suggest mutations; activity undo and doctor remediation can mutate. The service profile should expose only explicitly approved Mail and Disk tools and reject unlisted calls even if a client sends their names directly.

## Alternatives inspected

* `denis-samatov/yandex-workspace-mcp` (MIT, active 2026-09-24) has useful deny-by-default, path policy (`policies/paths.py`) and fixed-field audit (`security/audit.py`) patterns, but its code and deployment model include Wiki, multi-user OAuth, Redis and separate secret storage; adopting it would add a second backend and a wider surface.
* `gdigora/yandex-mail-mcp` (no detected license, last push 2025-12-22) is a smaller IMAP/SMTP Python server with search/read/send/move/delete; it lacks Disk and its `server.py` includes permanent-delete fallback semantics.
* `andrewmalov/mcp-imap` (`pyproject.toml` declares MIT, but no standalone license file appears in the tree; last push 2026-01-07) has IMAP/SMTP tools and tests but no Disk. It logs tool argument dictionaries in `server.py`, unsuitable for mail content without further work.
* `akinfold/hermes-yandex-mail` (MIT) is a capable Hermes plugin with IMAP search/read, flags, move, delete, attachment handling, and opt-in SMTP sending. Its action filtering and read-without-marking behavior are useful; however, it is a Hermes plugin rather than a ChatGPT/MCP server, includes mailbox mutation and delete features outside this release's tool surface, and expects app-password credential configuration rather than this deployment's OAuth token flow and canonical Bruce vault integration.
* `akinfold/hermes-yandex-disk` (MIT) is a capable Hermes plugin using the Disk REST API, with browse/read/write/share operations and per-tool filtering. It requests the appropriate Disk scopes and has its own test coverage; however, it is likewise a Hermes plugin rather than a ChatGPT/MCP base, expects a token in Hermes credential configuration, and its write/share surface and lifecycle are distinct from this fork's guarded MCP profile and central Bruce exact-key route.

Only the security ideas from the MIT donor are reused; no donor source is copied. Upstream repositories remain read-only.

Official references: [Yandex OAuth and PKCE](https://yandex.com/dev/id/doc/en/codes/code-url), [app registration](https://yandex.com/dev/id/doc/en/register-client), [Mail OAuth scopes and XOAUTH2](https://yandex.ru/support/yandex-360/business/mail/ru/web/security/oauth), [Mail IMAP/SMTP](https://yandex.com/support/yandex-360/business/mail/en/mail-clients/others), [Disk REST API](https://yandex.com/dev/disk/rest/).
