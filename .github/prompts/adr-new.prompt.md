# /adr-new

Write a new ADR for the decision just made.

1. Take the next free number from
   [id-registry.md](../../docs/00-index/id-registry.md).
2. Copy [0000-template.md](../../docs/20-architecture/adr/0000-template.md) to
   `docs/20-architecture/adr/NNNN-lowercase-kebab-title.md`.
3. Fill in Context (the **constraint** that forced the decision, not a
   preference), Decision (one imperative paragraph), Alternatives rejected (with
   the real reason each was rejected), and Consequences (including what a future
   session must not undo).
4. Add the row to [adr/README.md](../../docs/20-architecture/adr/README.md) and
   the number to the ID registry.
5. If it supersedes an existing ADR, mark the old one superseded - **never edit
   its content**.
