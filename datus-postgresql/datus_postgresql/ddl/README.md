# Datus PostgreSQL Storage DDL

该目录用于保存/生成 DDL（手动执行）。

生成方式（示例）：

```bash
python - <<'PY'
from datus_postgresql.storage_ddl import render_relational_ddl, render_vector_ddl

# 关系表 DDL
print(render_relational_ddl(schema="public"))

# 向量表 DDL（替换为你的 embedding 维度）
print(render_vector_ddl(vector_dim=384, schema="public"))
PY
```

说明：
- DDL 默认包含 `namespace` 字段，用于多空间隔离
- DDL 不在运行时自动创建，请在数据库中手动执行
