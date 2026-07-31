# Security Policy

NimHunt handles cryptocurrency transactions and location-based eligibility.
Security reports should therefore be treated carefully and privately.

## Supported version

The current `main` branch and the live deployment are the supported version.
Older commits and abandoned feature branches are not maintained releases.

## Reporting a vulnerability

Do not open a public issue when a report could expose:

- private signing material or credentials;
- a way to redirect, duplicate, suppress, or falsely confirm a payment;
- a method for bypassing claim, ownership, location, or Prizedraw rules;
- sensitive user or deployment information.

Use GitHub's private vulnerability-reporting option when it is available. If it
is not available, contact the repository owner privately through GitHub before
sharing technical details publicly.

Include the affected route or module, reproduction steps, expected and observed
behaviour, and whether any real funds or secrets may be at risk. Use testnet and
minimal values for reproduction whenever possible.

## Operational caution

Never attach mnemonics, passphrases, `.env` files, production databases, private
RPC credentials, or unredacted logs to an issue or pull request. On-chain
transfers are irreversible; ambiguous outgoing transactions must remain blocked
for reconciliation rather than being retried speculatively.

NimHunt has substantial automated regression coverage but has not received an
independent security audit. The repository documents its intended modest-use
scope and operational limitations in `README.md`.
