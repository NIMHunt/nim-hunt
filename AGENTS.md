# Repository Guidelines for Codex Tasks

## Scope
These instructions apply to the entire repository.

## Product Language
- Use **"Prizedraw"** consistently in user-facing copy, documentation, comments, branch/PR descriptions, and commit messages when referring to the product or feature concept.
- Avoid alternate spellings such as "Prize Draw", "prize draw", or "prize-draw" unless you are quoting existing external text or preserving a required API/schema name.

## UI and Styling
- Keep UI design and styling changes out of task scope unless the user explicitly requests them.
- Do not make opportunistic visual tweaks, layout changes, CSS refactors, copy restyling, or component redesigns while working on unrelated functionality.
- If a functional change requires a small UI adjustment, keep it minimal and call it out clearly in the final response.

## Nimiq and Money-Moving Logic
- Treat Nimiq transaction logic, settlement logic, wallet logic, and any money-moving or value-transfer behavior as high-risk.
- Prefer the smallest safe change when touching transaction construction, signing, fee handling, settlement updates, balance handling, recipient addresses, payment state transitions, or related persistence.
- Be explicit about assumptions and edge cases when changing this area.
- Prefer adding or updating tests for money-moving logic whenever practical, especially for transaction state transitions, amount calculations, fee behavior, idempotency, and failure handling.
- Do not mock away the critical money-moving behavior in tests unless the test also verifies the boundaries and data passed into the mock.

## Git Safety
- Never push to `main`.
- Do not force-push or rewrite shared branch history unless the user explicitly asks for it.
- Before committing, check the branch and working tree state so you do not accidentally mix unrelated changes into your commit.

## Local Development
- Keep local development simple.
- Prefer straightforward commands and minimal setup over adding new tooling, services, build steps, environment managers, or background daemons.
- Avoid introducing new dependencies unless they are clearly necessary for the requested task.
- Document any new required local setup in the README or another obvious place when a task truly needs it.
