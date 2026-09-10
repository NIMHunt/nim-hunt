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

## Claim-abuse containment

Public USER creation uses durable hashed-source-network quotas (five per hour
and twelve per rolling day by default), plus a generous global hourly ceiling.
Existing USERs are looked up before either quota is considered. Source network
is only a registration-abuse signal: shared Wi-Fi, carrier NATs and VPN exits do
not establish that several claims belong to one person.

Nimiq signatures prove control of a key, not a unique human. Claim limits bind
durably to that verified wallet. The Nimiq Pay payout address may legitimately
differ from the signing address and is not ownership-proven, so a shared payout
address is used for correlation but cannot by itself reject a claim. A burst of
first-claim wallet/device pairs against one to four Spots is manual-reviewed
only when those claims also converge on a payout address; an ordinary crowd of
new users at one Spot is not held merely for novelty, location, or shared IP.

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
