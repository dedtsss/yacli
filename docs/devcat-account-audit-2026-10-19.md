# Full account audit — 2026-10-19

Contract: [issue #551 delta](https://github.com/dedtsss/agent-dispatch/issues/551#issuecomment-6275345771). Starting production/source HEAD: `8af2b2fb7b36d7c08b5c867ba1faf11bbd351879`.

## Checkpoint: diagnosis and focused repair

The reported ChatGPT `mail.folders` calls for `dvabobra2014` and `ykkareliadom` timed out. The earlier loopback samples were dedts 64 folders / 10.29 s, ykkareliadom 10 / 10.10 s, dvabobra2014 client timeout at 18 s. These do **not** establish invalid OAuth.

Worker baseline with an initialized loopback MCP session succeeded for all three exact aliases: dedts 10.089 s, ykkareliadom 15.319 s, dvabobra2014 10.538 s. One connected-tunnel diagnostic on the unchanged production runtime succeeded for dedts in 17.431 s. This is a diagnostic comparison, not final published ChatGPT acceptance. The separate CatVPS Control initialization-required HTTP 409 belongs to that client's MCP/session route; it is not Yandex OAuth evidence.

Stage isolation with the service's existing credential context (no copied keys, no secret output):

| Alias | Central helper get, s | IMAP TLS connect, s | XOAUTH2 auth, s | LIST, s | Folders |
| --- | ---: | ---: | ---: | ---: | ---: |
| dedts | 9.862 | 0.346 | 0.194 | 0.050 | 64 |
| ykkareliadom | 9.464 | 0.281 | 0.213 | 0.114 | 10 |
| dvabobra2014 | 9.446 | 0.311 | 0.180 | 0.078 | 24 |

The central helper accounts for most measured latency. Its `get` path performs an exact-key existence check and read through SSH. This audit does not attribute either individual SSH duration or historical ChatGPT failures to an unmeasured cause. Code inspection and a focused delayed-helper regression establish a separate defect: provider I/O held the global HTTP session registry lock, coupling slow/abandoned calls to unrelated sessions. Health can remain 200 during that contention.

The repair moves DevCat tools into blocking workers after checking the initialized session and releasing the registry lock. A timed-out/disconnected HTTP caller no longer holds that registry hostage. An already started provider operation is not promised to roll back or stop; sends must never be retried automatically after an ambiguous transport failure. Vault reads now have a 15 s deadline, bounded output and Unix process-group cleanup, classified as NETWORK_ERROR on deadline. Nonzero/missing/empty vault results still fail closed, with no cache, local credentials or fallback. There is no blanket end-to-end cancellation/deadline guarantee for all provider transfers.

Discovery loads account metadata once and fails explicitly for unreadable/malformed/missing metadata or an allowlisted account missing from it. Every tool describes exact account identifiers, default dedts and service configuration; configured does not assert current authorization. Non-string accounts fail validation. Read annotations are explicit; upload is destructive because overwrite is supported; only Mail sending has openWorldHint=true.

No production deployment, service restart, capability expansion, OAuth repetition, user-data deletion or historical tool-disappearance attribution is part of this repair. The complete candidate matrix and final verification will be recorded below and in the companion JSON checkpoint.

## Recovery result — 2026-10-22

The 80-case historical traversal, including first failures, remains in the JSON.
`COMPLETE` means traversal, not acceptance. See the [working snapshot](devcat-recovery-2026-10-22.md)
for the first candidate, failed deployment/automatic rollback, exact network
replay, bounded repair, cold-start checks and current candidate matrix. The first
real deployment failed three read calls; earlier checksum success was candidate-only.

`mail.send_published_link` requires `mail.send` and review, not `disk.publish`.
Positive live execution is blocked only by the absence of a known safe existing
public test-object URL. The closed publication gate prevents creating a new
fixture. No new sends, uploads, scopes or additional Disk accounts were used.
Actual deployed/ChatGPT acceptance is still distinct from candidate verification.
