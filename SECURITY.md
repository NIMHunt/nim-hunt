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

Nimiq Pay device hashes are browser-supplied bearer identifiers, not proof of a
human visitor. Public deployments therefore limit creation of fresh device
accounts from one source network to 5 per hour and 12 per day by default;
existing accounts remain usable. Operators can tune these ceilings with
`NIMHUNT_USER_REGISTRATION_HOURLY_LIMIT_PER_IP` and
`NIMHUNT_USER_REGISTRATION_DAILY_LIMIT_PER_IP`. Railway's validated
`X-Real-IP` value is used at the public edge, so the application origin must not
be exposed in a way that lets clients bypass or forge Railway's proxy headers.
These controls protect account storage and visitor metrics. They do not replace
the signed-wallet session required at money-moving claim boundaries.

NimHunt has substantial automated regression coverage but has not received an
independent security audit. The repository documents its intended modest-use
scope and operational limitations in `README.md`.
