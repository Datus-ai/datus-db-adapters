# Oracle HR Sample Schema (vendored)

`hr_create.sql`, `hr_populate.sql` and `hr_code.sql` are copied verbatim from
Oracle's official sample schemas:

- Upstream: <https://github.com/oracle-samples/db-sample-schemas> (`human_resources/`)
- Schema version 21, release 03-FEB-2022
- License: MIT — Copyright (c) 2023 Oracle. The notice is retained at the top
  of each file.

The upstream `hr_install.sql` orchestrator is deliberately **not** vendored: it
drives the installation through interactive `ACCEPT` prompts and unconditionally
drops an existing HR user. `../../init/02_install_hr_schema.sh` performs the
same steps (create user, grant privileges, run the three scripts) idempotently
and unattended, which is what a container start-up hook needs.

The schema installs 7 tables holding 216 rows in total:

| Table | Rows |
|-------|------|
| `regions` | 5 |
| `countries` | 25 |
| `locations` | 23 |
| `departments` | 27 |
| `jobs` | 19 |
| `employees` | 107 |
| `job_history` | 10 |

It is used by `tests/integration/test_sample_schema_hr.py` to exercise metadata
extraction against structures the TPC-H fixtures do not cover: foreign keys, a
self-referencing manager hierarchy, a view, and PL/SQL procedures and triggers.

To refresh the vendored copies:

```bash
cd datus-oracle/docker/sample-schemas/human_resources
for f in hr_create hr_populate hr_code; do
  curl -fsSLO "https://raw.githubusercontent.com/oracle-samples/db-sample-schemas/main/human_resources/$f.sql"
done
```
