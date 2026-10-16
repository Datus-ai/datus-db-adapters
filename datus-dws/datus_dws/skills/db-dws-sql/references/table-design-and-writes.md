# DWS table design and writes

## Table design

Storage orientation and distribution are creation-time decisions that normally require recreating the table to change. Choose them from workload evidence; when evidence is absent, say so rather than guessing.

- `WITH (orientation=column)` for analytical scans over few columns of a wide table — the common default for a warehouse. `orientation=row` for point lookups and frequent single-row updates.
- `compression=low|middle|high` applies to column storage; higher compression trades CPU for I/O.
- `DISTRIBUTE BY HASH (col)` for large tables. Choose a high-cardinality column that appears in joins and grouping, so that joins can run co-located and data lands evenly. A low-cardinality key causes skew, and a skewed node bounds the whole query.
- `DISTRIBUTE BY REPLICATION` for small dimension tables that join against many facts — every node holds a full copy, removing redistribution at the cost of storage.
- `DISTRIBUTE BY ROUNDROBIN` when no column is a good hash key; it distributes evenly but cannot support co-located joins.
- Join keys that match the distribution keys of both sides avoid a redistribution step. When they do not match, DWS redistributes or broadcasts — correct, but the dominant cost in large joins.

Partitioning uses the Oracle-style range form:

```sql
PARTITION BY RANGE (dt) (
    PARTITION p2026 VALUES LESS THAN ('2027-01-01'),
    PARTITION pmax VALUES LESS THAN (MAXVALUE)
)
```

Partitioning and distribution are independent and compose: partitions prune by predicate, distribution spreads across nodes.

## DDL portability

`pg_get_tabledef()` returns the authoritative definition including `orientation`, `compression`, `DISTRIBUTE BY`, `TABLESPACE` and `TO GROUP`. Two of those clauses are properties of the cluster that produced them:

- `TO GROUP <node_group>` names a node group of the source cluster.
- `TABLESPACE <name>` names a tablespace; on storage-decoupled clusters these are OBS-backed (`cu_obs_tbs`, `default_obs_tbs`).

Keep both when describing an existing table. Remove both before replaying the DDL against another cluster, or creation fails.

## Materialized views

Supported only on cluster version 8.2.1.220 or later **and** with `enable_matview` set to `on`. A new cluster ships with it off. Confirm both before generating materialized-view SQL; if either is unmet, use a regular view or a scheduled table instead.

## Write constraints

- Prefer set-based statements over row-by-row DML; distribution key columns cannot be updated.
