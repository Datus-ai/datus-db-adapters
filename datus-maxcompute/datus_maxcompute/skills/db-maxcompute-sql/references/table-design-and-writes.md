# MaxCompute writes and materialized views

## Writes and partitions

- `INSERT INTO` appends; `INSERT OVERWRITE TABLE` replaces the target table or static partition. Keep the `TABLE` keyword for overwrite statements.
- Insert mappings are positional, not name-based. Explicitly order target and source expressions to match the destination schema.
- For a static partition, use constant partition values and omit those partition columns from the `SELECT` list.
- For dynamic partitions, place partition expressions last in the `SELECT` list and in the same order as the dynamic partition declaration.
- Generate `UPDATE` or `DELETE` only when metadata or the user confirms a Transactional or Delta table. Ordinary tables do not support row-level updates or deletes.
- Do not generate `BEGIN`, `START TRANSACTION`, `COMMIT`, `ROLLBACK`, `GRANT`, or `REVOKE`; the adapter rejects them.

## Views and materialized views

- Treat ordinary views as logical definitions.
- MaxCompute materialized views store data and have stricter SQL limitations. In particular, do not use window functions or UDTFs in a materialized-view definition, and do not assume another engine's refresh syntax.
