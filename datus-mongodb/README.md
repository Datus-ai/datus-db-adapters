# MongoDB adapter

The adapter implements the Datus `BaseSqlConnector` contract for read-only
MongoSQL and also exposes native document methods for one configured database.

Install `datus-mongodb[sql]` for SQL with Arrow and pandas output. The adapter's
wheel includes the MongoSQL translator. PyMongo executes the resulting pipeline.
Configure a MongoDB URI, a database, and a BSON `schema_file` for the collections
queried with SQL.

```python
import os

from datus_mongodb import MongoDBConnector

config = {"uri": os.environ["MONGODB_URI"], "database": "sales", "schema_file": "sales-schema.json"}
with MongoDBConnector(config) as mongo:
    result = mongo.execute_query("SELECT order_no, status FROM orders LIMIT 5", result_format="list")
```

SQL supports read-only, single-collection SELECTs. The connector returns
lists, CSV, pandas, or typed Arrow. Document methods can be used without
the SQL extra or a schema file. Schema bootstrap also uses native document
queries to index collection and view definitions and sample documents.

```python
import os

from datus_mongodb import MongoDBConnector

with MongoDBConnector({"uri": os.environ["MONGODB_URI"], "database": "sales"}) as mongo:
    collections = mongo.list_collections()
    metadata = mongo.describe_collection("orders")
    recent = mongo.find("orders", {"status": "paid"}, sort=[("ordered_at", -1)], limit=5)
    totals = mongo.aggregate("orders", [{"$group": {"_id": "$customer.city", "total": {"$sum": "$amount"}}}])
```

Use a MongoDB account with the `read` role. JSON query arguments use MongoDB Extended JSON.
The optional `schema_file` is a JSON document keyed by database and collection. Its collection
entries are MongoDB BSON schemas, shared with Dosi. Sampled paths are returned separately
from the declared schema.

## Testing

Building or installing from source requires a current stable Rust toolchain.
From the workspace root, run `uv run --package datus-mongodb pytest datus-mongodb/tests/unit`.
For integration tests, start `docker compose -f datus-mongodb/docker-compose.yml up -d --wait`.
Then run `uv run --package datus-mongodb --extra sql pytest datus-mongodb/tests/integration -m integration`.
