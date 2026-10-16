# TiDB table design

## Table design

- No distribution or bucketing clause exists — do not emit `DISTRIBUTED BY`, `DUPLICATE KEY`, `AGGREGATE KEY`, or `PROPERTIES (...)`; those are StarRocks and Doris syntax.
- Prefer `BIGINT AUTO_RANDOM PRIMARY KEY` over `AUTO_INCREMENT` for high write rates: a monotonically increasing key concentrates writes on one region. `AUTO_INCREMENT` values are unique but not gap-free or globally monotonic — never treat them as an ordering.
- An integer `PRIMARY KEY` is `CLUSTERED` by default (the row is stored in the primary-key index); `SHOW CREATE TABLE` renders this as a `/*T![clustered_index] CLUSTERED */` comment alongside `/*T![auto_rand] AUTO_RANDOM(n) */`. Preserve both when round-tripping DDL.
- `FOREIGN KEY` is supported (TiDB 6.6+), unlike in StarRocks and Doris.

## CTAS

- CREATE TABLE ... AS SELECT is unsupported; issue CREATE TABLE then INSERT INTO ... SELECT.
