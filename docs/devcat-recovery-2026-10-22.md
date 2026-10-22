# Issue #551 working snapshot — 2026-10-22

Objective: reliable three-account Mail and main-account Disk through the existing
ChatGPT MCP route, preserving the central vault, capability gates and one owner.
Branch `manual/yandex-mcp-551`, PR #1. Current normative rules blob:
`0179ab4122f3a5bc086d123e25876c1c4c9eea7a`.

## Material state and first deployment

Checkpoint `4f49e756bcca59ee377bc5f592a0118955665dcb` isolated provider I/O from the
HTTP session registry, bounded vault reads and fixed account/service discovery
and annotations. The preserved DNS/TCP repair was committed at
`4e19741bd2eb4e2e8aa03ff4abe5f029cfa3c537`. Its candidate passed 411 tests and
loopback verification; those successes are retained in
[the first candidate report](devcat-first-candidate-2026-10-22.json).

The controller subsequently obtained its privileged root route and ran the exact
prepared installer once, PID 2420376. Worker left source/artifact/deployment
untouched until the terminal result existed and PID exit was confirmed. Result:
**ROLLED_BACK**. Both existing services are active; `current` is again
`/opt/yacli-devcat/releases/8af2b2fb7b36d7c08b5c867ba1faf11bbd351879`.

[First deployment evidence](devcat-first-deployment-2026-10-22.json) preserves all
three NETWORK_ERROR failures unchanged:

| Tool / account | Seconds |
| --- | ---: |
| mail.list / dedts | 12.020 |
| mail.search / dvabobra2014 | 11.776 |
| disk.read / dedts | 29.553 |

The first deployed verifier saved error codes and timings, not the underlying
messages. The earlier Disk checksum PASS was **candidate-only**. No ChatGPT UI
acceptance follows from either loopback matrix.

## Bounded diagnosis and repair

One read-only replay under connect/poll/getsockopt-only syscall tracing reproduced
Disk read failure in 30.684 seconds. On the failing API request DNS answered
immediately; IPv4 TCP connect to `cloud-api.yandex.net:443` remained pending until
the 20-second request deadline. IPv6 fallback returned ENETUNREACH. The trace did
not capture credential buffers, headers, mail content or signed URLs. See the
[sanitized network evidence](devcat-network-evidence-2026-10-22.json).

Mail replay succeeded after a separate five-second DNS delay. Six bounded fresh
TCP probes all connected by IPv4; IPv6 was unreachable. The exact stage of the
two historical deployed Mail failures cannot be reconstructed from their saved
codes alone. No OAuth failure, permanent provider outage, or global DNS cause is
asserted. No host network/resolver change was made in this recovery.

The repair is restricted to connection establishment and read requests:

- Mail retries the address pass at most once after a transient TCP failure,
  before TLS/authentication/submission, under the **same** 12-second DNS+TCP
  budget (DNS up to 8 seconds, each address up to 2 seconds). Permanent connection
  failures do not trigger that extra pass. No SMTP operation is replayed.
- Only Disk info, private metadata and download-ticket requests use a separate
  read client. The existing reqwest retry facility allows **one retry of a GET
  network failure**, scoped to the API host, with a 4-second connect timeout and
  one shared 20-second request deadline. HTTP denials are not retried. Mutation
  and transfer clients retain their existing policies; no blanket all-method
  policy, potentially committed write replay, or new upload was introduced.
- The verifier now retains URL-redacted network details for future failures and
  makes mail.list the first real provider call after server startup. No new
  credential store, token cache, account, scope, tunnel or service was introduced.

## Verification and remaining delivery

`cargo fmt --all -- --check`, `cargo clippy --locked --workspace --all-targets --
-D warnings`, and `cargo test --locked --workspace -- --test-threads=1` pass:
**416 tests, zero failures**. The five new regressions prove transient TCP recovery
and permanent failure behavior, successful recovery from a dropped GET, the one
retry maximum, and no POST/403 replay. The existing TCP deadline regression also
passes. The locked release binary SHA-256 is
`b18d8690a1ec5f03f2dd3f90647560725c1524f229e2c7a154c83e7fad2b362e`.

[Cold-start evidence](devcat-cold-start-2026-10-22.json): four independent fresh
server processes pass dedts list/read, dvabobra2014 search/read, main Disk list,
and main Disk read as their first provider request. Cold Disk read checks all
64 bytes against SHA-256
`567c5857734748376f22492c59322ca1376057f09c1b4683c7d60da1b9643304`.
These are cold **candidate** checks, not proof of deployed or ChatGPT reliability.

The [current matrix](devcat-recovery-results-2026-10-22.json) is the final
integration pass for this repaired binary. The original 80-case audit and first
failures remain in [historical evidence](devcat-account-audit-2026-10-19.json);
`COMPLETE` there means traversal, not acceptance. Additional Disk accounts remain
NOT_CONFIGURED. Successful historical SMTP submissions and upload are not
repeated. Personal Plus remains the actual worker route (`gpt-6.1-sol/high`);
local runner telemetry shows no exhausted-limit signal. No second worker exists.

**Published-link acceptance correction:** `mail.send_published_link` sends an
already-published URL. Its exact capability is `mail.send`, plus the normal review
digest; it does **not** require `disk.publish` or Disk credentials. Positive live
execution remains **BLOCKED_FIXTURE** because there is no known safe existing
public URL for an approved test object. The disabled `disk.publish` capability
prevents creating a new fixture here; it is not this tool's execution gate.
Preview and negative review coverage remain valid. No publication or extra SMTP
submission is performed to make this row green.

The controller's existing privileged CatVPS host route is now available for one
final bounded apply; this worker still owns diagnosis/code/check/build/push and
does not bypass privileges. After the repaired final head is committed/pushed,
`target/devcat-delivery/manifest-2026-10-22-repair.json` pins its exact source HEAD,
report hash and binary, while retaining the original manifest/artifact. The
non-mutating installer preflight must pass against the unchanged baseline.

Single controller host action:

```sh
python3 /home/codex/yandex-mcp-551/ops/devcat-yandex/install-verified.py \
  --manifest /home/codex/yandex-mcp-551/target/devcat-delivery/manifest-2026-10-22-repair.json \
  --apply
```

The action refuses any source/report/artifact/deployed-HEAD mismatch before
activation; verifies the existing services and tunnel control-plane readiness;
installs the exact binary in a new pinned release; saves atomic rollback;
switches `current`; and restarts only the existing MCP service. It checks the
running process hash, both services, catalog/root/account isolation, real
read-only calls and tunnel readiness, or restores the previous binary. Terminal
host result and postdeploy JSON go under `target/devcat-delivery`. This is one
complete finite host action, not a series of chat-operated batches. ChatGPT
acceptance still requires actual calls through the published connector after a
successful deployment. Do not present candidate or loopback PASS as that result.

## Current checkpoint

Repaired release candidate: **PASS**, 30 PASS + 6 EXPECTED_DENY, all three aliases
and the main Disk checksum. Four cold starts: **PASS**. Full suite: **416 PASS**.
Source/binary artifacts are ready for the single controller host action above.
Production remains on the verified rollback baseline; no second apply has been
launched by this worker. Status: **READY_FOR_CONTROLLER_APPLY**; actual deployed
and ChatGPT connector acceptance remain pending. The available controller route,
expected baseline, pinned manifest and terminal JSON paths are concrete; no
additional implementation worker or chat-operated item-by-item loop is required.
