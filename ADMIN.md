# NimHunt administrator panel

The administrator panel is deliberately separate from ordinary NimHunt user identity and from every Nimiq signing secret.

## Configure the administrator password

1. From the repository root, run:

   ```bash
   python scripts/hash_admin_password.py
   ```

2. Choose a strong password of at least 16 characters and store the plaintext password in your password manager.
3. The script prints a `scrypt$...` value. In Railway, add a service variable named:

   ```text
   NIMHUNT_ADMIN_PASSWORD_HASH
   ```

4. Paste the generated `scrypt$...` value as that Railway variable and redeploy.
5. Visit `/admin`. There is intentionally no public NimHunt link to this route.

Railway stores only the password hash used by the app. The NimHunt seed/mnemonic is never accepted by the admin login form.

## Session security

Admin sessions are signed with an in-memory key, expire after 30 minutes, use `HttpOnly` and `SameSite=Strict` cookies, and use the `Secure` cookie flag on public deployments. Restarting/redeploying NimHunt invalidates all existing admin sessions.

Every state-changing admin form is protected by a session-bound CSRF token. Login attempts are rate-limited in memory to five failed attempts per client in fifteen minutes.

## Spot banning

Spot banning is intentionally severe and should be rare.

The browser never supplies a payout amount or destination. The server:

1. marks the Spot `BANNED` immediately so it is no longer public/claimable;
2. prevents creation of any new claim payout transaction for that Spot;
3. fails claims/entries that do not already have a committed payout transaction;
4. waits for any transaction that was already pending when the ban happened to resolve, because an already-broadcast transaction cannot safely be assumed cancelled;
5. calculates the confirmed unspent Spot balance from NimHunt's transaction ledger; and
6. submits that entire remaining balance to the fixed operator address configured by `NIMHUNT_SPOT_FEE_ADDRESS`.

The ban form requires all of the normal admin protections plus re-entering the admin password and typing `BAN <spot-id>` exactly.

A creator cancellation already in progress cannot be converted into a Spot ban. This prevents two competing refund/sweep workflows from acting on the same Spot.

## Audit data

The panel creates two additive SQLite tables on demand, without changing the core NimHunt schema version:

- `ADMIN_AUDIT_LOG` records moderation actions and notes.
- `ADMIN_SPOT_BAN` records the durable state of severe Spot bans and their balance sweep.

The normal transaction reconciler also revisits pending Spot-ban sweeps so a restart does not cause a second send or lose the moderation state.
