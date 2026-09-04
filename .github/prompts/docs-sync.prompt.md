# /docs-sync

Run the
[Documentation Maintenance Protocol](../../docs/50-process/docs-maintenance-protocol.md)
for the changes in the working tree.

1. Classify the change against
   [documentation-map.md](../../docs/00-index/documentation-map.md).
2. Resolve the file set, always including `status.md` and `CHANGELOG.md`.
3. Update the glossary first if a concept was added or renamed.
4. Apply the updates, respecting mutability. Bump `updated:`.
5. Register new identifiers in `id-registry.md`.
6. Run `just docs-check`.
7. Emit the `DOCS SYNC` block.
