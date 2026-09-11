# Security architecture and audit map

This document describes the current security architecture as code, not as a
claim that NimHunt can prove physical presence or personhood. It is an audit map
for trust boundaries, module ownership, durable state and transaction ordering.

## Trust hierarchy

| Fact or signal | Trust level | Permitted use |
|---|---|---|
| Valid Nimiq signature and server-derived address | Cryptographic key-control fact | Bind a session and immutable payout destination. It does **not** identify a unique human. |
| SQLite claim, capacity, transaction intent, reservation and winner state | Authoritative server fact | Admission, idempotency, financial accounting and settlement. |
| Chain-confirmed transfer read through configured Nimiq RPC | External financial fact, subject to RPC availability/finality policy | Deposit confirmation and conservative funding history. Browser-reported transaction hashes alone never fund a Spot. |
| Device identifier | Continuity signal | Find a USER and apply per-device limits. It is neither authentication nor personhood. |
| Browser coordinates and accuracy | Attacker-controlled assertion | Radius rules and behavioural evidence after binding into a signed message. Signing proves approval of the assertion, not presence. |
| Source IP and IP geolocation | Advisory network evidence | Rate admission and corroboration. A NAT or VPN exit is not a human identity. |
| Exact-centre, inside-only and reward-targeting patterns | Weak behavioural evidence | Finite public-claim restriction; never proof or an automatic permanent ban by themselves. |
| Funding relationships | Weak external corroboration | Finite/corroborating restriction after service-like sources are suppressed; never proof of common ownership. |

The final defence is financial blast-radius containment. It remains necessary
when every heuristic is evaded.

## Ownership and dependency direction

```text
claim_identity       security_metadata       claim_http
(key verification)   (transaction-neutral)   (bounded ASGI plumbing)
       \                    |                     /
        +------------- claim_security -------------+
                         | orchestration/audit
 evidence guards --------+-------- settlement guards
                         |
                    db_access/database
```

* `claim_identity.py` owns device-format validation, address canonicalisation,
  the time-bounded Nimiq signature subprocess, and signer derivation. Its names
  state the distinction between continuity and verified key control.
* `security_metadata.py` owns only exact-key JSON mechanics and the bounded
  timestamp-bucket primitive. It never starts or commits a transaction and does
  not impose one lifecycle on semantically different state.
* `claim_http.py` owns raw ASGI body collection/replay and security JSON errors.
  It documents that request-size middleware must be outside this boundary.
* `claim_network_security.py` owns trusted source extraction directly. It no
  longer imports or mutates `claim_security`.
* `claim_authorization.py` owns the versioned, canonical claim message and
  fixed-point location representation.
* `claim_security.py` remains the explicit authentication/authorization HTTP
  coordinator, claim audit recorder and fail-closed payout-record gate. It keeps
  compatibility aliases for established extension and test APIs, but no longer
  implements cryptographic, generic metadata, ASGI, or source-network primitives.
* `fresh_claim_guard.py` owns account/signer history, first-location checks and
  meaningful activity. Password Spot exemptions occur here only.
* `claim_location_guard.py` owns authoritative impossible-travel state at the
  claim-creation boundary; it is not a general browser-location scorer.
* `location_behavior_guard.py` owns bounded weak location-behaviour evidence.
* `wallet_cluster_guard.py` owns conservative funding-source evidence.
* `claim_security_defence_in_depth.py` owns broad coordinated-burst checks and
  server-derived wallet binding at creation. Its wrappers remain explicit debt.
* `claim_wallet_hourly_limit.py` owns durable per-wallet/payout claim frequency.
* `claim_payout_throttle.py` owns atomic global, daily, individual and Open Spot
  exposure reservation/accounting.
* `claim_settlement_security.py` normalises deferred settlement so security holds
  are not misreported as payment failures.
* `claim_payout_diagnostics.py` exposes private operational hold diagnostics.
* `claim_security_maintenance.py` performs bounded cursor-based cleanup.
* `claim_security_response_delivery.py` preserves response/background-task order.
* `request_body_limit.py` is the outer, streaming-aware request-size boundary.
* `draft_creation.py` bridges durable draft admission to bounded external address
  derivation without holding a SQLite writer lock.
* `trans_updater.py`, `settlement_updater.py`, `refund_address_safety.py` and
  `cancellation_safety.py` own transaction intent, reconciliation, settlement,
  final recipient/refund safety and cancellation leases.

Dependency direction is toward primitives and persistence. Feature guards may
call `db_access`; primitives never import policy/orchestration. The remaining
runtime wrappers are installed in documented order in `funding_flow.py`.

## End-to-end flows

### Identity and session

1. Public device bootstrap calls USER lookup/creation. New rows are admitted in
   a writer transaction through durable hashed-source and global timestamp rows;
   existing devices do not spend admission capacity.
2. `/api/security/challenge` validates the device continuity identifier, admits
   per-source and per-device rate buckets atomically, then stores a random,
   expiring wallet-auth message.
3. `/api/security/verify` has a separate pre-verification CPU-abuse admission.
   It atomically claims/removes the challenge before running the Node signature
   helper outside that transaction. The helper derives the signer address.
4. Verification binds USER, device and signer durably and creates a random,
   hashed-cookie, expiring server session. Shared source IP does not block many
   independently device-limited wallet sessions.
5. Session lookup requires cookie, device match, expiry, canonical signer and a
   still-matching USER row. Maintenance removes expired challenges, sessions,
   authorizations and rate buckets in bounded passes.

### Find Spots and evidence

Find Spots bootstrap/search is read-oriented. Claim-status requests use the
session when present and record only bounded meaningful/behavioural state.
Browser GPS can be observed for first-location continuity, exact-centre,
inside/outside and reward-targeting patterns. Own Spots are excluded from
reward-target evidence. IP geolocation is bounded and advisory. Impossible
travel uses claim-validated canonical Spot anchors; funding evidence uses bounded
chain history and suppresses service-like broad sources. Weak evidence produces
finite restrictions or corroboration, not a generic score or permanent ban.

### Claim authorization by product path

All four initial paths—Open Standard, Password Standard, duration Standard, and
Prizedraw entry—require an authenticated session and versioned claim-specific
signature in public deployments. The prepared message binds action, Spot,
device, signer-derived receiving wallet, fixed-point browser location/accuracy,
environment, network, nonce, issue time and expiry.

The durable state machine is `issued -> verifying -> processing -> retired`.
`verifying` is committed before the expensive signature subprocess, making one
nonce good for at most one verification attempt. Field mutation, another Spot,
another action, another wallet, expiry or reuse fails. The business claim route
then atomically enforces current capacity and consumes a Password code where
applicable. Successful creation records the immutable signer payout and claim
security record; absence of that record makes later payout fail closed.

Open/Password/duration share this initial boundary. Prizedraw entry uses the
same action and identity binding but later follows persisted winner selection.
No browser payout field has financial authority.

### Duration claims

The initial duration claim is claim-specifically signed. Subsequent detail and
location heartbeats require the device-bound authenticated session, but heartbeat
GPS and health inputs remain browser-supplied evidence and are **not** separately
wallet-signed. Health/staleness and location processing decide completion or
failure; only completed eligible claims reach settlement. Heartbeat signing is
intentionally deferred because interactive wallet signing on every heartbeat
would change UX.

### Settlement

Immediate and deferred Standard settlement, duration completion, and persisted
Prizedraw winners all converge on durable outgoing-intent logic. A winner is
persisted before attempted payout and is not redrawn after failure. Before money
may leave, gates re-read claim status, immutable recipient, security record,
manual-review state, per-wallet/individual limits, global rolling/daily limits,
and applicable Open Standard rolling/lifetime exposure. Reservations occur in a
SQLite writer transaction. Ambiguous broadcasts retain local intent and require
reconciliation instead of blind retry. Remainder/cancellation leases exclude
unpaid successful claims and competing money movement. Operator release clears
only the reviewed hold; it does not erase lifetime payout accounting.

### Creator and funding lifecycle

Ordinary create and duplicate reserve `draft_creation_admission` under the same
rolling per-user/global policy. Stale pending rows are reclaimed; consumed rows
continue to count for the rolling window. Only after admission succeeds is a
monotonic deposit key allocated. Address derivation runs outside writer
transactions behind a cancellation-safe semaphore: cancellation of the waiter
does not release capacity while the worker still runs. Final insertion rechecks
editable-draft capacity and consumes the reservation. Failed work never reuses a
key index. Deposit submission is only a hint; chain verification confirms funds
and creation fee destinations/amounts are immutable. Publish, cancellation and
refund use server ownership/state and financial leases.

### Public infrastructure

`RequestBodyLimitMiddleware` is outside claim security and rejects declared or
streaming/chunked over-limit bodies with 413 before the security layer buffers
and replays them. Reverse geocoding has bounded cache, same-key coalescing,
provider concurrency, timeout/circuit breaker and fallback. Public
`/transaction-healthz` returns minimal health; detailed holds remain private.
Source extraction accepts only validated Railway `X-Real-IP`, otherwise the ASGI
peer, and never trusts arbitrary forwarded chains.

## Password Spot policy table

| Protection | Password Spot | Open Spot |
|---|---:|---:|
| Device-bound server session | yes | yes |
| Claim-specific wallet signature/replay protection | yes | yes |
| Server-derived immutable payout | yes | yes |
| Radius, capacity and one-time code consumption | yes | radius/capacity yes |
| Impossible-travel guard | yes | yes |
| Fresh account/signer and first-location gate | exempt | yes |
| Weak exact-centre/inside-only/reward-target evidence | exempt from public-only restriction | yes |
| Funding-cluster public gate | exempt through public policy | yes |
| Global/daily/individual payout exposure | yes | yes |
| Open Spot rolling/lifetime exposure | no (creator secret changes admission) | yes |
| Transaction intent, reconciliation and cancellation safety | yes | yes |

The exemption is deliberately narrow: possession of a finite creator-issued
secret changes admission, not authentication, payout identity or settlement.

## Verification map

| Boundary | Current owner | Characterisation |
|---|---|---|
| Global/daily/individual and Open Spot exposure, durable reservation/release accounting | `claim_payout_throttle.py`, `claim_security.py` | `test_claim_payout_throttle.py`, `test_claim_security.py`, `test_financial_finality.py` |
| Signer-derived immutable payout and legacy mismatch hold | `claim_identity.py`, `claim_security_defence_in_depth.py`, `claim_security.py` | `test_claim_payout_identity.py`, `test_find_spots_claim_identity.py`, `test_claim_security_defence_in_depth.py` |
| Canonical short-lived one-time authorization | `claim_authorization.py`, `claim_security.py` | `test_claim_authorization.py`, `test_claim_authorization_lifecycle.py`, `test_claim_security_route_boundary.py` |
| Fresh account/activity and first IP/GPS safeguards | `fresh_claim_guard.py` | `test_fresh_claim_guard.py`, `test_fresh_claim_guard_routes.py` |
| Shared-network-compatible behaviour and continuity | `claim_auth_abuse_guard.py`, `fresh_claim_guard.py`, `claim_security_defence_in_depth.py` | `test_claim_auth_abuse_guard.py`, `test_user_registration_security.py`, `test_claim_security_defence_in_depth.py` |
| Exact-centre, inside-only, reward-targeting and own-Spot exclusion | `location_behavior_guard.py` | `test_location_behavior_guard.py` |
| Bounded conservative funding clusters/service suppression | `wallet_cluster_guard.py` | `test_wallet_cluster_guard.py` |
| Request body, reverse geocode bounds and minimal public health | `request_body_limit.py`, reverse-geocode code in `public_html.py`, diagnostics | `test_resource_exhaustion_boundaries.py`, `test_robustness_guards.py` |
| Shared draft admission, monotonic keys, cancellation-safe derivation and source-network verification | relational admission in `db_access.py`, `draft_creation.py`, create/duplicate routes | `test_resource_exhaustion_boundaries.py`, `test_spot_duplication.py`, `test_claim_auth_abuse_guard.py` |
| Shared primitives retain caller transaction and bounded replay semantics | `security_metadata.py`, `claim_http.py`, `claim_identity.py` | `test_security_architecture_primitives.py` |

## Durable metadata and table inventory

| Family | Cardinality/value bound | Expiry and cleanup | Deletion/replay notes |
|---|---|---|---|
| `claim_security:challenge:*` | attacker can create random keys only after bounded source/device admission; small dict | challenge TTL; cursor maintenance max 500/pass | deleted before verification; absence rejects |
| `claim_security:session:*` | one random hashed key per successful verification | session TTL; cursor maintenance | absence requires reauthentication |
| `claim_security:user:*` | at most one per USER | durable continuity; no expiry | malformed/missing fails payout/session binding closed |
| `claim_security:claim_authorization:*` | timestamp-sortable random key per admitted preparation | issuance cleanup 64 oldest plus maintenance; TTL/grace | deletion cannot replay; absence rejects |
| `claim_security:claim:*` | one bounded audit dict per CLAIM | durable financial record, no generic cleanup | deletion blocks payout; never enables it |
| `claim_security:rate:*` | source/device/wallet-derived bounded keyspaces; list capped by limit | rolling-window pruning and maintenance | deletion only resets rate admission, not authorization |
| `claim_security:recent_events` | one list capped by `MAX_RECENT_EVENTS` and retention | pruned on read/write | weak evidence only |
| `claim_security:latest_incident` | one bounded incident | overwritten | diagnostics only |
| `claim_security:payout_throttle_reservations` | one list, pruned against durable payout/intents | reservation expiry/reconciliation | financial semantics remain in dedicated owner; corruption fails conservative paths |
| `fresh_claim_guard:*` rollout/activity/GPS/location | O(USER), fixed counters/anchors or capped days | finite timestamps; USER-bound rows not globally expired | malformed weak evidence is neutral, not a ban |
| `fresh_claim_guard:signer:*` | O(observed signer), one cache record | positive/negative/failure cache TTL | attacker needs admitted signed wallet |
| `location_behavior_guard:user:*` | one per USER; saturated counters and threshold-capped Spot IDs | finite restriction; state remains bounded | malformed weak state migrates neutral |
| `claim_location_suspicion:*` | at most one per USER | cleared after cooldown/trusted resolution | malformed state is explicitly cleared; claims still face normal rules |
| `wallet_cluster_guard:origin:*` | one per observed signed wallet | cache window | bounded chain pages |
| `wallet_cluster_guard:source:*` | one per observed source; member map capped | old members pruned/cache refreshed | broad service source suppresses restriction |
| `user_registration_security:recent:*` | one global plus one per hashed source; each timestamp list limited by daily admission | old timestamps pruned when source is used | source keys are hashed/normalised upstream, not arbitrary request text |
| deposit key allocator metadata | one integer counter | monotonic, never cleaned/reused | allocation is durability-critical |
| `spot_remainder_settled:*` | at most one per Spot | durable settlement marker | prevents repeated remainder settlement |
| `draft_creation_admission` table | bounded by admitted attempts over configured rolling retention plus reclaimable pending rows | stale pending reclaimed; consumed retained for rolling count | explicit relational reservation |
| claims, transaction intents, payout/winner fields | relational, one/few per business entity | financial retention | source of truth for idempotency and reconciliation |

Cleanup can temporarily lag a burst because passes are bounded, but new row
creation is itself admitted and periodic passes use a durable cursor, so cleanup
cannot be permanently pinned to the lexicographically first rows. High-value
replay and financial facts deliberately are not subject to generic expiry.

## Writer transaction and concurrency inventory

| Boundary | Invariant protected | External work under lock? |
|---|---|---|
| USER registration admission + insert | existing users bypass quota; one new USER consumes source/global allowance atomically | no |
| challenge/verify/authorization rate admission | competing requests cannot overspend the same bucket | no |
| challenge consume / authorization `issued -> verifying -> processing` | one signature attempt and one business request per nonce | signature subprocess is **after** `verifying` commit |
| claim creation and Password code consume | final capacity, ownership/wallet binding and code consumption agree | no |
| impossible-travel/behaviour state update | observations cannot lose updates | no |
| payout slot reservation | global/daily/Spot/lifetime observations and reservation are one serial decision | no broadcast while reservation lock is held |
| outgoing transaction intent | one active intent per financial action; ambiguous send is not retried blindly | network send follows durable intent |
| Prizedraw winner selection | winner is persisted before payout and never redrawn due to send failure | no |
| cancellation/remainder lease | cancellation cannot refund value owed to an unpaid successful claim or race settlement | chain/send work follows durable lease/intents |
| draft admission + key allocation | quotas and globally monotonic non-reused index | no derivation |
| derivation semaphore | bounded subprocess concurrency; cancelled waiter cannot prematurely release slot | external worker is intentionally inside semaphore, outside SQLite transaction |
| draft final insert/recheck | simultaneous editable cap and reservation consumption remain atomic after derivation | no |
| deposit confirmation | competing reports cannot spoof/double-confirm funding; chain evidence is obtained before final serialized update where required | RPC is kept outside long writer sections |

Transactions are not shortened merely for style: each listed read must remain
consistent with its dependent write.

## Configuration groups and validation notes

* **Identity/session:** `NIMHUNT_CLAIM_AUTH_*`, authorization TTL/grace/cleanup,
  Node binary. TTLs and limits are seconds/counts and must be positive.
* **Registration/auth admission:** `NIMHUNT_USER_REGISTRATION_*` and verify
  source/device limits. Source capacity is intentionally much larger than device
  capacity for venues.
* **Evidence:** `NIMHUNT_CLAIM_IDENTITY_*`, signer-history, first-location,
  GPS, behavioural, travel and funding-cluster variables. Distance names include
  metres and speed names metres-per-second; legacy `*_MPS` names remain for
  compatibility.
* **Financial exposure:** payout rolling/daily/individual and Open Spot rolling/
  lifetime settings. Zero lifetime percentage is an intentional emergency hold
  mode; defaults are unchanged.
* **Resources/external services:** body bytes, IP geolocation, reverse-geocoder
  limits, derivation commands/concurrency and history page/sample caps.

The complete operator-facing names, defaults and units are in
[`configuration.md`](configuration.md).

Renaming environment variables or centralising all configuration would risk
silent deployment drift. A follow-up should add a typed startup snapshot and
cross-field checks (for example grace >= verifier timeout and positive window
relationships) with deprecation aliases, rather than change production policy
inside an unrelated refactor.

## Creator/private-route session migration design (deferred)

Creator/self routes currently use the device identifier as ownership continuity
for Create Spot detail/edit/delete, My Spots, My Claims, claim-code retrieval,
deposit intent/submitted, publish, cancel, duplicate and display-name changes.
This is substantial and is deliberately deferred rather than folded into an
unrelated change.

A follow-up should:

1. Generalise the signed server session from “claim session” to account session
   without changing the signer-derived claim payout invariant.
2. Bootstrap it lazily when Create Spot, My Spots or My Claims first performs a
   private operation; explain expiry and offer one reauthentication/retry path.
3. Require USER/device/session agreement on all listed endpoints. Page GETs may
   remain public shells, but private JSON must fail closed.
4. Preserve pending-deposit recovery across expiry by reauthenticating the same
   durable USER/signer binding rather than replacing browser state.
5. Update `browser_utils.js`, Create Spot, duplicate, My Spots/My Claims and
   claim-code clients to share one session bootstrap and retry helper.
6. Consider fresh wallet step-up for cancellation, claim-code retrieval and
   display-name changes; publishing and deposit reporting need session auth but
   chain verification remains authoritative.
7. Ship compatibility telemetry first, then enforcement, with endpoint-level
   tests for cross-device access, expiry, reauthentication and no UX loops.

## Remaining concerns and recommended follow-ups

1. Migrate creator/self routes to signed server sessions as designed above.
2. Replace the remaining monkey-patch install chain with explicit named call
   composition in small, independently testable PRs—settlement first, then
   preclaim—without changing ordering.
3. Add duration-heartbeat authorization only after a non-interactive wallet-safe
   protocol and replay model are designed.
4. Keep the pinned Nimiq SDK and helper lockfile under periodic dependency audit.
5. Add operational metrics for metadata creation/cleanup lag, reservations,
   manual-review age, provider circuit state and ambiguous intents.
6. Consider relational migrations for high-cardinality sessions/authorizations
   and payout reservations only with measured query/cleanup benefit and an
   explicit fail-closed migration plan.
7. Add typed, grouped configuration parsing and cross-field startup validation.

Accepted limitations remain unlimited independent-wallet creation,
sophisticated GPS spoofing, VPN/network rotation, real-device farms, relays and
collusion, and the inability to prove one wallet equals one human.
