# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for a security problem.

Use GitHub's private reporting instead:
[Report a vulnerability](https://github.com/anrysys/tg-ai/security/advisories/new).

Include what you did, what happened, and what you expected. A proof of concept
helps. You will get an acknowledgement as soon as the report is seen.

## What counts as a vulnerability here

This project holds a Telegram session file - a full credential for a real
person's account - and a plaintext archive of every private conversation on that
account. The threat model is written up in
[docs/70-ops/security.md](docs/70-ops/security.md), including the named risk
register (`RISK-01` .. `RISK-07`).

Reports in any of these areas are especially welcome:

- **A way to send a message the Send Guard should have refused.** The guard
  blocks messages to peers with no history that are not saved contacts. It has no
  override flag, and any bypass is a vulnerability
  ([ADR-0005](docs/20-architecture/adr/0005-hard-block-send-to-unknown-peers.md)).
- **A way to reach the archive from off-host.** PostgreSQL binds to
  `127.0.0.1:5434` on purpose; the database holds every private message in
  plaintext.
- **SQL injection.** Search queries originate from an LLM reading arbitrary chat
  content. Every value is bound as a parameter; a path where one is interpolated
  is a vulnerability.
- **Prompt injection that survives across turns.** A Dialog Persona is stored and
  re-injected into a drafting context on every read (`RISK-07`). Persona fields
  are sanitised and fenced; a way to smuggle an instruction through them, or to
  forge or terminate the fence, is a vulnerability.
- **Anything that writes a credential where it can be committed.** `.env` and
  `*.session` are git-ignored and the session file is mode `0600`.

## What is not a vulnerability

- The fact that this software can send messages from your account. That is the
  purpose of the project, and the disclaimer in the [README](README.md) covers
  the account-ban risk that comes with it.
- Telegram rate-limiting or banning an account that has been used to send an
  unusual volume of messages.
- Anything requiring an attacker who already has read access to your machine and
  your session file. At that point the account is already compromised by any
  measure.

## Supported versions

This project is pre-1.0 and is developed on `main`. Fixes land there.
