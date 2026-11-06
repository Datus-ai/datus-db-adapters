# datus-databricks

Databricks SQL warehouse adapter for Datus.

## Installation

```bash
pip install datus-databricks
```

This will automatically install the required dependencies:

- `datus-db-core`
- `datus-sqlalchemy`
- `databricks-sql-connector`
- `databricks-sdk`

## Configuration

The adapter is registered when the package is installed. Point it at a SQL warehouse.

### Namespace mapping

Unity Catalog has two levels above a table: catalog → schema → table. Datus has three: catalog → database → schema → table. This adapter maps them as:

| Datus | Databricks |
|---|---|
| `catalog` | Unity Catalog catalog |
| `database` / config `schema` | Unity Catalog schema |
| `schema` | unused |

So `get_databases()` runs `SHOW SCHEMAS` and returns Unity Catalog schemas. There is no third namespace, so `get_schemas()` always returns `[]`.

OAuth uses a Databricks CLI profile. The adapter stores the profile name only. The CLI keeps the token in its own cache, and the SDK refreshes it. A personal access token is the other mode, for CI or a service account. Set one of `profile` or `access_token`.

```yaml
namespace:
  analytics:
    type: databricks
    host: adb-0000000000000000.azuredatabricks.net
    warehouse_id: 0123456789abcdef
    catalog: main
    schema: analytics
    auth_mode: oauth_profile
    profile: dev
    # auth_mode: access_token
    # access_token: dapi...
```

`warehouse_id` is sent as `/sql/1.0/warehouses/<id>`. Set `http_path` instead when the target is not a SQL warehouse, for example a cluster path under `/sql/protocolv1/`.

`timeout_seconds` defaults to 120 because a serverless warehouse may spend most of the first call waking up.

## Run SQL

Generated statements go through `execute()`. The adapter classifies each statement and runs the kinds it supports:

- **Read:** `SELECT`, `SHOW`, `EXPLAIN`
- **Write:** `INSERT`, `UPDATE`, `DELETE`, `MERGE`
- **Structure:** `CREATE`, `DROP`, `ALTER`
- **Session state:** `SET`

Each statement is sent to the SQL warehouse. Databricks applies the change when the statement succeeds.

## Set up a CLI profile

Do this once on the machine that will run Datus, as the same OS user. The adapter reads that user's Databricks CLI cache. It does not load a `.env` file and it does not store the token.

1. Install the [Databricks CLI](https://docs.databricks.com/en/dev-tools/cli/install.html) and confirm it is on your `PATH`:

   ```bash
   databricks --version
   ```

2. If this network needs an HTTP proxy, export it in the same shell before logging in. The CLI uses these variables for the browser login and for later token refresh. Skip this step on a network that reaches the workspace directly.

   ```bash
   export ALL_PROXY=http://proxy.example.com:8888
   export HTTP_PROXY=$ALL_PROXY
   export HTTPS_PROXY=$ALL_PROXY
   export http_proxy=$ALL_PROXY
   export https_proxy=$ALL_PROXY
   ```

3. Log in to the workspace. `--host` is the workspace URL. `--profile` is the name you will put in the Datus config. Pick any name. `dev` matches the example above.

   ```bash
   databricks auth login \
     --host https://adb-0000000000000000.azuredatabricks.net \
     --profile dev
   ```

   A browser opens. Finish the sign-in there. The CLI writes the profile to `~/.databrickscfg` and the refresh token to its cache under your home directory.

4. Confirm the profile is usable. The `Valid` column must be `YES`.

   ```bash
   databricks auth profiles
   ```

   ```text
   Name  Host                                                     Valid
   dev   https://adb-0000000000000000.azuredatabricks.net         YES
   ```

5. Use that profile name in the namespace config. `host` there is the same workspace hostname without `https://`.

Start Datus from a process that can read this home directory. If you exported a proxy for the login, export the same variables for the Datus process. The SDK and the SQL connector read `HTTPS_PROXY` and `ALL_PROXY` from the environment. A proxy set only in one terminal does not apply to an app launched another way.

When `databricks auth profiles` shows `NO`, or a later call reports an invalid refresh token, run `databricks auth login` again in a shell that already has the proxy variables set.
