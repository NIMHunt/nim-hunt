## Summary

<!-- What problem does this solve, and what is the smallest safe change? -->

## Scope

<!-- List the files or product areas intentionally changed. -->

## Safety boundary

- [ ] This does not change wallet, transaction, payout, refund, fee, settlement,
      Prizedraw winner-selection, or database-finality behaviour.
- [ ] Or: the financial/blockchain impact and protected invariant are explained
      below.

<!-- Delete the line that does not apply and explain any high-risk change. -->

## Verification

- [ ] Focused regression tests added or updated
- [ ] Full Python test suite
- [ ] Node helper tests
- [ ] Ruff and Python compilation
- [ ] JavaScript, helper, and shell syntax checks
- [ ] Jinja template compilation
- [ ] Fresh development database seed
- [ ] Dependency checks and audits

## Manual checks

<!-- Include browser, phone, Nimiq Pay, TestAlbatross, or deployment checks. -->

## Repository hygiene

- [ ] No secrets, local databases, generated caches, logs, or temporary
      diagnostic files are included
- [ ] Documentation and comments match the final behaviour
