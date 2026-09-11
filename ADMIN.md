# NimHunt administrator panel

The administrator panel is deliberately separate from ordinary NimHunt user identity and from every Nimiq signing secret. It is intended for the operator of a small NimHunt deployment, not as a general multi-user staff system.

There is intentionally no public navigation link to the panel. Visit `/admin` directly.

## What the panel provides

The dashboard gives the operator a compact view of the deployment:

- active and total users;
- active and total Spots;
- pending and total reports;
- a 30-day new-user graph;
- an all-time Spot-creation leaderboard;
- pending report review; and
- a persistent moderation audit log.

Reports can be approved or dismissed with moderator notes.

Users have three moderation states:

- **ACTIVE** — normal NimHunt behaviour;
- **LIMITED** — may continue to claim, but cannot create new Spots; and
- **BANNED** — cannot create Spots or make new claims.

Severe Spot banning is a separate action with additional financial safeguards and is documented below.

## Configure the administrator password

1. From the repository root, run:

   ```bash
   python scripts/hash_admin_password.py
   ```

2. Choose a strong password of at least 16 characters and store the plaintext password in your password manager.
3. The script prints a `scrypt$...` value. In Railway or the deployment secret manager, add a service variable named:

   ```text
   NIMHUNT_ADMIN_PASSWORD_HASH
   ```

4. Paste the generated `scrypt$...` value as that variable and redeploy.
5. Visit `/admin` and sign in with the original plaintext password, not the `scrypt$...` value.

If `NIMHUNT_ADMIN_PASSWORD_HASH` is absent or malformed, admin login fails closed. The rest of NimHunt remains available, but the administrator panel cannot be authenticated until a valid hash is configured.

The deployment stores only the password hash used by the app. The NimHunt seed/mnemonic is never accepted by the admin login form and must never be reused as the admin password.

## Session security

Admin sessions are signed with an in-memory key, expire after 30 minutes, use `HttpOnly` and `SameSite=Strict` cookies, and use the `Secure` cookie flag on public deployments. Restarting or redeploying NimHunt invalidates all existing admin sessions.

Every state-changing admin form is protected by a session-bound CSRF token. Login attempts are rate-limited in memory to five failed attempts per client in fifteen minutes.

A deployment restart also clears the in-memory login-failure counters. This does not weaken the requirement for a strong administrator password; the rate limit is an additional guard rather than the primary secret protection.

## Spot banning

Spot banning is intentionally severe and should be rare. It is not the same operation as creator cancellation.

Before using it in production, verify that `NIMHUNT_SPOT_FEE_ADDRESS` is the intended operator-controlled address. The browser never supplies a payout amount or destination. The server:

1. marks the Spot `BANNED` immediately so it is no longer public or claimable;
2. prevents creation of any new claim payout transaction for that Spot;
3. fails claims or entries that do not already have a committed payout transaction;
4. waits for any transaction that was already pending when the ban happened to resolve, because an already-broadcast transaction cannot safely be assumed cancelled;
5. calculates the confirmed unspent Spot balance from NimHunt's transaction ledger; and
6. submits that entire remaining balance to the fixed operator address configured by `NIMHUNT_SPOT_FEE_ADDRESS`.

The ban form requires all of the normal admin protections plus re-entering the admin password and typing `BAN <spot-id>` exactly.

A creator cancellation already in progress cannot be converted into a Spot ban. This prevents two competing refund or sweep workflows from acting on the same Spot.

For production smoke testing, inspect the dashboard and use harmless report or user-moderation actions first. Do not use a funded Spot ban merely to prove that the panel works: the action is intentionally capable of moving the Spot's remaining confirmed funds.

## Audit data

The panel creates two additive SQLite tables on demand, without changing the core NimHunt schema version:

- `ADMIN_AUDIT_LOG` records moderation actions and notes.
- `ADMIN_SPOT_BAN` records the durable state of severe Spot bans and their balance sweep.

No separate database migration command is required for the administrator panel.

The normal transaction reconciler also revisits pending Spot-ban sweeps so a restart does not cause a second send or lose the moderation state.

## Deployment checklist

Before enabling administrator access on a public deployment:

1. generate a unique admin password and `scrypt$...` hash;
2. store `NIMHUNT_ADMIN_PASSWORD_HASH` in the deployment secret manager, never in the repository;
3. confirm `NIMHUNT_SPOT_FEE_ADDRESS` is the intended operator-controlled address;
4. redeploy and confirm ordinary NimHunt pages still work;
5. visit `/admin`, sign in, and verify the dashboard loads; and
6. keep the plaintext admin password in a password manager rather than a shell script, `.env` file committed to Git, issue, pull request, or log.

See [`docs/configuration.md`](docs/configuration.md) for deployment and backup
requirements, [`README.md`](README.md) for the project overview, and
[`SECURITY.md`](SECURITY.md) for vulnerability-reporting guidance.
