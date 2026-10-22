# Recovery checkpoint — 2026-10-22

Single execution owner continues `manual/yandex-mcp-551`, PR #1, from
`4f49e756bcca59ee377bc5f592a0118955665dcb`. Preserved the existing `src/mail.rs`
TCP/DNS repair and the complete historical JSON matrix. No audit restart and no
repeat of successful SMTP submissions.

Canonical `main` task rules blob verified:
`d22115b68d2c7b50332eecc02c1d3f63d0833918`.
Source read/write, Git reads, GitHub GET and loopback health canaries passed in
the current sandbox. The supported escalated execution route runs as `codex`
and permits Git metadata writes. It does not grant general sudo or writes to
`/opt/yacli-devcat`; production installation requires a supported privileged route.

Expected production target remains
`/opt/yacli-devcat/releases/8af2b2fb7b36d7c08b5c867ba1faf11bbd351879`.
Both existing services are active. No production changes made at this checkpoint.

Historical evidence: the previous worker measured intermittent libc DNS stalls;
a single resolver restart restored six bounded queries. Current `resolvectl`
queries for IMAP and Disk API succeed in milliseconds. A historical IMAP
quoted/literal comparison returned the same 1991 matches for `yandex`; a selected
subject word returned zero. This does not establish a search serialization bug.

Next: finish focused TCP regressions; use a known matching message as the search
control; verify list/read for each alias and exact bytes of the existing dedicated
Disk test file. Preserve first failures separately from current results. Build one
verified artifact, push the same PR, and deploy only with exact expected-head/hash
checks and atomic rollback; otherwise deliver one complete host action.

Additional Disk accounts stay NOT_CONFIGURED. Publishing remains disabled.
ChatGPT UI acceptance has not been established by loopback calls.

## Terminal result

Implementation and final candidate verification: **PASS**. Production delivery:
**BLOCKED_HOST_DEPLOY**. The unchanged production symlink and binary were checked
again; both existing services remain active. No installation or restart occurred
in this recovery. ChatGPT UI acceptance remains **NOT_TESTED**.

The actual runner is `gpt-6.1-sol`, reasoning `high`, Personal Plus. Its local
runner events exposed Plus rate-limit telemetry with no exhausted-limit signal;
no Business fallback or second worker was started. Chat tool telemetry alone was
not used to infer remaining quota.

The preserved mail repair bounds DNS to 8 seconds, each TCP address to 2 seconds,
and DNS plus TCP to 12 seconds. It caches only resolved addresses for 30 seconds
and evicts them on connection failure. It does not cache credentials. TLS/socket
I/O retain their existing 20-second waits; this is not a blanket whole-request
or cancellation guarantee. Three focused regressions cover stalled-address
failover, total TCP budget, and address-cache expiry/failure eviction.

Final checks: `cargo fmt --all -- --check`, `cargo clippy --locked --workspace
--all-targets -- -D warnings`, and `cargo test --locked --workspace --
--test-threads=1`: **411 passed, zero failed**. Release build uses `--locked`.

Verified release binary SHA-256:
`a0654c1958ab1d56e6d471d1d07c35f80053a82093ce999ac098109e7b9b7a37`.

| Current release-candidate result | dedts | ykkareliadom | dvabobra2014 |
| --- | --- | --- | --- |
| folders / list / read listed UID | PASS | PASS | PASS |
| positive indexed search / read hit / search same known UID | PASS | PASS | PASS |
| Disk list / read uploaded checksum fixture | PASS | NOT_CONFIGURED | NOT_CONFIGURED |
| SMTP submit | historical PASS; not repeated | historical PASS; not repeated | historical PASS; not repeated |
| send_published_link execution | BLOCKED: publication disabled | BLOCKED: publication disabled | BLOCKED: publication disabled |

The Disk fixture was uploaded successfully in the original audit; recovery only
read that same dedicated object. All 64 bytes match SHA-256
`567c5857734748376f22492c59322ca1376057f09c1b4683c7d60da1b9643304`.
No replacement upload or send was needed. Positive search uses a provider-indexed
existing hit, reads its UID, then proves that the same UID is returned by search.
The previous zero-match subject controls remain historical evidence; a protocol
change was not justified by the quoted/literal comparison.

The final JSON records current timings and results separately from the original
80-case matrix. It also verifies all 11 schemas, exact aliases/default, read
annotations, omitted-account resolution to dedts, root/account/type isolation,
NOT_CONFIGURED additional Disk accounts, and the closed publish gate. Search
rechecks reached about 25 seconds for dedts; no lower latency SLA or ChatGPT UI
acceptance is claimed. Intermediate harness mistakes and the correctly enforced
64 KiB read boundary are identified in the separate probe-history JSON.

## One prepared host action

The available escalated exec route runs as `codex`; `/opt/yacli-devcat` is not
writable, and `sudo -n -l` provides no Yandex deployment/service privilege.
Neither CatVPS control tool is exposed in this session. Desktop Commander reports
CatVPS offline. These are route/privilege blockers, not missing owner permission.
No Docker privilege workaround, extra service/tunnel, network change or OAuth
scope change was attempted.

After the final commit, `target/devcat-delivery/manifest-2026-10-22.json` pins that
exact source HEAD, candidate report hash, binary hash, expected deployed HEAD and
previous binary hash. The binary is preserved beside it as `yacli-2026-10-22`.
Run this single action through the existing privileged CatVPS host route:

```sh
python3 /home/codex/yandex-mcp-551/ops/devcat-yandex/install-verified.py \
  --manifest /home/codex/yandex-mcp-551/target/devcat-delivery/manifest-2026-10-22.json \
  --apply
```

It refuses a source/report/artifact/deployed-HEAD mismatch before activation;
checks the existing services and tunnel control-plane readiness; installs the
verified artifact in a new pinned release; saves an atomic rollback pointer;
switches `current`; and restarts only the existing MCP service. It verifies the
running process hash, both services, tunnel readiness, catalog, root/account
isolation and actual read-only Mail/Disk calls. Failure restores the previous
symlink and service and records the rollback result. The operator action and
postdeploy verifier record terminal JSON under `target/devcat-delivery` without
mail contents, UIDs, signed links or credential values. Atomic switching/rollback
and fail-closed guards were checked locally; the privileged installation path has
not been executed here. This action establishes loopback acceptance only.
