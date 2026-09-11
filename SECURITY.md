# Security Policy

NimHunt handles cryptocurrency transactions, location-based eligibility and a private administrator panel. Security reports should therefore be treated carefully and privately.

## Supported version

The current `main` branch and the live deployment are the supported version.
Older commits and abandoned feature branches are not maintained releases.

## Reporting a vulnerability

Do not open a public issue when a report could expose:

- private signing material or credentials;
- the administrator password, `NIMHUNT_ADMIN_PASSWORD_HASH`, admin session cookies or other authentication material;
- a way to bypass the `/admin` login, CSRF, reauthentication or moderation safeguards;
- a way to redirect, duplicate, suppress, or falsely confirm a payment;
- a method for bypassing claim, ownership, location, or Prizedraw rules;
- sensitive user or deployment information.

Use GitHub's private vulnerability-reporting option when it is available. If it
is not available, contact the repository owner privately through GitHub before
sharing technical details publicly.

Include the affected route or module, reproduction steps, expected and observed
behaviour, and whether any real funds or secrets may be at risk. Use testnet and
minimal values for reproduction whenever possible. Do not perform a real funded
Spot ban merely to demonstrate an administrator-panel issue unless that movement
of funds is itself necessary to reproduce the vulnerability.

## Operational caution

Never attach mnemonics, passphrases, admin passwords or hashes, `.env` files,
production databases, private RPC credentials, session cookies, or unredacted
logs to an issue or pull request. On-chain transfers are irreversible; ambiguous
outgoing transactions must remain blocked for reconciliation rather than being
retried speculatively.

The administrator password must be distinct from every Nimiq mnemonic or signing
secret. Generate its stored `scrypt$...` value with
`scripts/hash_admin_password.py` and keep the plaintext password in a password
manager. See `ADMIN.md` for administrator-panel deployment and moderation safety.

NimHunt has substantial automated regression coverage but has not received an
independent security audit. The repository documents its intended modest-use
scope and operational limitations in `README.md`.

## Architecture and trust model

The current implementation is documented in the developer-facing
[security architecture and audit map](docs/security-architecture.md). That map
traces identity, claim authorization, location evidence, settlement, creator
funding, metadata lifecycle, writer transactions, configuration, Password Spot
semantics and accepted limitations.

NimHunt trusts verified key control, durable server state and chain-confirmed
financial facts for the narrow purposes they establish. Device identifiers,
source networks, browser geolocation, behavioural patterns and wallet-funding
relationships remain continuity or corroborating evidence. They do not prove a
unique human or physical presence. Security therefore combines cryptographic
authorization, explicit weak evidence, financial exposure ceilings, durable
transactional invariants and bounded resource consumption.

### Decision classes

* Invalid authentication, authorization, capacity, radius or replay facts reject
  the request immediately.
* Fresh-account, first-location, behavioural and funding evidence can make Open
  public claims temporarily unavailable; weak evidence alone is not silently
  converted into a permanent ban.
* Impossible-travel escalation can use the existing explicit cooldown/ban state.
* Missing claim audit, payout-identity mismatch, individual/Open Spot exposure
  and operator-visible exceptions hold settlement for manual review.
* Global rolling/daily payout capacity defers settlement until capacity returns.

### Resource security

The outer request-body middleware rejects oversized declared and streaming
bodies before claim-security buffering. Reverse geocoding is cache-bounded,
coalesced, concurrency-limited and circuit-broken. Draft creation and duplication
share durable admission; monotonic deposit indexes are never reused; external
derivation is bounded and runs outside SQLite writer transactions. Public
transaction health is intentionally minimal.

## Claim-abuse containment

Public USER creation uses durable hashed-source-network quotas (forty per ten-minute burst, 120 per hour,
and 300 per rolling day by default), plus a generous global hourly ceiling.
Existing USERs are looked up before either quota is considered. Source network
is only a registration-abuse signal: shared Wi-Fi, carrier NATs and VPN exits do
not establish that several claims belong to one person.

Nimiq signatures prove control of a key, not a unique human. Claim limits bind
durably to that verified wallet. For new claims, NimHunt pays the canonical
Nimiq address derived from the public key that authenticated the claimant; the
same address is stored as the claim's immutable payout snapshot. Legacy claims
whose signer and stored payout differ are held for manual review rather than
rewritten or paid automatically. Fresh-wallet Sybil attacks remain possible,
so coordinated-burst and financial-containment controls remain necessary.

Automatic claim payouts have atomic short-window count/amount limits, rolling
24-hour count/amount limits, and an absolute per-payout ceiling. Aggregate
limits defer work until their rolling window clears. An over-ceiling individual
payout instead sets a durable `individual_automatic_payout_limit` manual-review
marker in the claim-security record. It remains visible through payout
diagnostics and survives restart because it is stored in SQLite. Settlement
rechecks the marker before reserving another slot or sending, so retries are
cheap and cannot bypass the hold. After investigating the claim and arranging
an appropriate configured ceiling or manual process, an operator deliberately
clears the marker with `claim_security.release_claim_manual_review()`; it is
never released merely because time elapsed.

Open Standard Spots also have independent rolling and lifetime exposure budgets.
By default, no more than 10 payouts or 5,000 NIM (whichever is reached first)
can be reserved automatically for one Open Spot in 24 hours. In addition, no
more than 50% of that Spot's configured claim capacity or reward pool may ever
be paid automatically. Whichever count/value/rolling/lifetime boundary is
strictest wins. The lifetime boundary does not reset, so waiting 24 or 72 hours
cannot progressively drain the remainder automatically.

At the default 50%, Open Spots configured for 1, 4, 10, 20, and 50 claims permit
respectively 0, 2, 5, 10, and 10 automatic payouts (the 50-claim Spot reaches the
rolling absolute count boundary first). A one-claim Open Spot therefore always
requires manual review: without a creator-issued or otherwise scarce admission
credential, automatically paying one untrusted claimant necessarily risks 100%
of its reward pool. Wallet, device, IP, and GPS heuristics cannot solve that
inherent case.

Claims beyond either per-Spot boundary retain their existing status and
entitlement but receive a durable manual-review marker with the Spot id, observed
count/amount, configured bounds and reason. The atomic SQLite reservation is
shared by concurrent workers and survives restart/redeployment. An operator can
release an inspected claim with the existing
`claim_security.release_claim_manual_review()` helper; release bypasses only the
per-Spot automatic boundary for that claim, while global limits remain
authoritative. Configure the policy with
`NIMHUNT_OPEN_SPOT_PAYOUT_WINDOW_SECONDS`,
`NIMHUNT_OPEN_SPOT_PAYOUT_MAX_COUNT`, and
`NIMHUNT_OPEN_SPOT_PAYOUT_MAX_NIM`. The lifetime percentage is configured with
`NIMHUNT_OPEN_SPOT_LIFETIME_AUTOMATIC_PERCENT`; setting it to `0` is an
emergency mode that sends every Open Standard Spot payout to manual review.

Code-protected Standard Spots are deliberately unchanged: their finite,
creator-issued, single-use claim codes are an additional admission signal and
already bound participation to the configured Spot capacity. Prizedraw
settlement is likewise outside this Open Standard Spot containment rule.
# Claim authorization boundary

Every new public Standard claim (open or code-protected), Prizedraw entry, and
duration-claim start is approved with a version 2 Nimiq signed message prepared
by the server. The message binds the Spot, device, fixed-point location,
accuracy, deployment environment, Nimiq network, server nonce and lifetime to
the verified session wallet. That wallet remains the immutable receiving
address. Authorizations are durably single-use; raw signatures and nonces are
not retained after successful use.

The durable authorization state machine is `issued → verifying → processing →
retired`. The `verifying` transition is committed before the one permitted Node
signature-verifier invocation. Successful claim audit recording retires the
challenge immediately; rejected attempts are also retired. Expired `issued`
rows and worker-abandoned `verifying`/`processing` rows are reclaimed in small,
oldest-first batches during later issuance. Missing or retired random challenge
IDs are terminal failures, so cleanup cannot re-enable replay. If a worker dies
after claim creation but before its audit marker, payout remains fail-closed;
the non-issued authorization cannot create another claim and is later cleaned.

Duration heartbeats continue to use the authenticated session. Nimiq Pay's
interactive `sign()` would otherwise prompt every heartbeat, substantially
degrading the duration flow; heartbeat-specific signing is deferred until a
non-disruptive wallet mechanism is available.

> A valid claim signature proves that the receiving wallet authorised the
> reported location. It does not prove the receiving wallet was physically at
> that location.
