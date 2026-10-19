# Local Inceptor compatibility environment

This **manual** environment exercises the Hive adapter against Transwarp TDH
Community Development Edition 2024.5 (Inceptor/Quark). It is not registered in CI.
It creates its own Compose project and persistent data volume; it does not reuse
an existing TDH deployment. Manager IDs and service IDs are discovered at runtime.

## Prerequisites

- Docker Compose v2 with `--wait`, at least 16 GiB Docker memory, 8 CPUs and enough
  free disk space for the roughly 5.4 GB archive, imported image and service data.
  The vendor image is amd64; on Apple Silicon Docker runs it through emulation.
- Python 3.12 and the **working tree** Hive adapter installed in an environment
  with pytest (for example the workspace `.venv`). For Kerberos, also install the
  `datus-hive[kerberos]` extra, native Kerberos headers/libraries, and `kinit`.
- Import the **official** TDH CE 2024.5 image yourself. It is not available from a
  public registry and is not distributed by this repository. Download the
  [vendor product archive](https://transwarp-ce-1253207870.cos.ap-shanghai.myqcloud.com/TDH-CE-2024-5/%E7%A4%BE%E5%8C%BA%E5%BC%80%E5%8F%91%E7%89%88%E4%BA%A7%E5%93%81%E5%8C%85/TDH-Standalone-Community-Transwarp-2024.5-X86_64-final.zip),
  extract its image archive, and load it with `docker load -i <image-archive>`.
  Verify `docker image inspect tdh-standalone:2024.5` succeeds. Compose has
  `pull_policy: never` so a missing image fails immediately.
- A valid vendor evaluation or license. The tested image offered a seven-day
  evaluation; continued use follows the normal [vendor licensing process](https://cloud.transwarp.cn/home).
  If activation is required, complete it in Manager. No license, vendor image,
  keytab or credential is included here. The persistent volume retains service
  data and licensing state across restarts.

The container requires `privileged` for the vendor's bundled service manager.
Published ports bind to loopback. This fixture is intended for a local workstation.

Run the following commands from **this directory**, with the adapter environment
activated. For a workspace `.venv`, activation is `source ../../../.venv/bin/activate`
when starting from `datus-hive/local/inceptor`.

## Initialize and check NONE authentication

```sh
docker compose up -d inceptor
python manage.py init --timeout 1800
export HIVE_HOST=localhost HIVE_PORT=19110 HIVE_USERNAME=hive HIVE_AUTH=NONE
unset HIVE_PASSWORD HIVE_KERBEROS_SERVICE_NAME
python manage.py wait --timeout 1800
INCEPTOR_TEST=1 python -m pytest ../../tests/integration/inceptor/test_queries.py \
  ../../tests/integration/inceptor/test_metadata.py -m integration -v
```

Manager is at http://127.0.0.1:18190 (vendor initial login `admin` / `admin`).
`INCEPTOR_MANAGER_USER` and `INCEPTOR_MANAGER_PASSWORD` override script credentials
when changed in Manager. The script waits for the normal authenticated Manager
API, associates the local node, initializes default configuration including
uninstalled template dependencies, and installs License, ZooKeeper, HDFS, YARN
and Quark. It waits for every operation; a failed, cancelled or warning operation
fails initialization. It does not install Aquila, Guardian or Hyperbase runtimes.

The Compose healthcheck checks **Manager only**. `manage.py wait` uses the actual
Hive adapter to authenticate, write and read an ORC table through an executor,
create a view, and exact-check databases, table/view separation and column
metadata. It drops its uniquely named probe database afterwards. Each attempt
has a 90-second process timeout; failed authentication or a broken executor
cannot make readiness pass. If an attempt is killed, a `datus_readiness_*`
database can remain for manual cleanup. Regression tests similarly create and
remove only uniquely named `datus_probe_*` databases. The test user needs DDL/DML
privileges for those objects. Setup or connection errors fail an opted-in run.

You may override published ports with `INCEPTOR_MANAGER_PORT`,
`INCEPTOR_HS2_PORT` and `INCEPTOR_KDC_PORT` exported in your shell. Set `HIVE_PORT`
to the matching HS2 port when testing. Use `COMPOSE_PROJECT_NAME` for a separate
project. Kerberos state lives in this directory's ignored `state/`; use separate
checkouts if running multiple realms concurrently. Keep port settings in the
shell rather than `.env` so Python and Compose see the same values.

## Add a local Kerberos KDC

```sh
python manage.py kerberos-enable --timeout 900
export KRB5_CONFIG="$PWD/state/kerberos/host-krb5.conf"
export KRB5CCNAME="FILE:$PWD/state/kerberos/client.ccache"
kinit -k -t "$PWD/state/kerberos/client.keytab" hive@DATUS.TEST
export HIVE_HOST=localhost HIVE_AUTH=KERBEROS HIVE_KERBEROS_SERVICE_NAME=hive
unset HIVE_PASSWORD
python manage.py wait --timeout 900
INCEPTOR_TEST=1 python -m pytest ../../tests/integration/inceptor/test_queries.py \
  ../../tests/integration/inceptor/test_metadata.py \
  ../../tests/integration/test_kerberos_auth.py -m integration -v
```

The KDC image builds from Ubuntu 24.04 and requires package download access.
Generated realm keys and keytabs stay under ignored `state/kerberos`, with
restrictive host permissions. Its healthcheck obtains a real ticket. The client
principal is `hive@DATUS.TEST`; the HS2 service is `hive/localhost@DATUS.TEST`.
Use `localhost`, as its name is part of the service principal. Client ticket
acquisition and renewal are explicit; the adapter does not run `kinit` for you.

This fixture authenticates **HS2 only**. HDFS, YARN and ZooKeeper retain the CE
SIMPLE configuration. The runtime helper configures the HS2 keytab, local realm,
executor settings, disables ZooKeeper SASL/discovery and trusts only the local
container address and loopback for the SIMPLE DataNode. It does not represent a
fully secured Hadoop cluster. Both Server and Executor restart after changes.
The original runtime files are backed up before the first change; Manager
configuration regeneration can overwrite these local test overrides. Run
`kerberos-enable` again after regeneration or a container restart to reapply them.
Do not change the test realm's HS2 hostname/principal independently.

The extra six Kerberos checks cover authenticated queries and database discovery,
ticket identity, rejection of NONE/omitted auth, a nonexistent service principal,
and a missing client ticket cache. The query/metadata suite runs under the same
Kerberos credentials and asserts Unicode, typed results, ORC writes/reads,
table/view classification, English/Chinese comments, DDL, samples and formats.

CE 2024.5 rejects physical column names containing spaces or hyphens with server
error 20484. The shared table contract therefore uses `MixedCase` and
`special_name`; separate tests assert that DDL restriction and verify quoted
query aliases containing spaces/hyphens. This engine constraint does not imply
support for every Hive SQL feature.

To restore NONE:

```sh
python manage.py kerberos-disable --timeout 900
export HIVE_AUTH=NONE
unset HIVE_KERBEROS_SERVICE_NAME
python manage.py wait --timeout 900
docker compose --profile kerberos stop kdc
```

## LDAP / CUSTOM endpoints

This Compose environment provisions NONE and Kerberos. LDAP directories and
CUSTOM password-provider deployment remain external setup steps. The same strict
manual tests can validate either already configured endpoint:

```sh
export HIVE_HOST=localhost HIVE_PORT=19110 HIVE_USERNAME=hive
export HIVE_AUTH=LDAP  # or CUSTOM
# Set HIVE_PASSWORD in your local shell; do not commit it.
unset HIVE_KERBEROS_SERVICE_NAME
INCEPTOR_TEST=1 python -m pytest ../../tests/integration/inceptor -m integration -v
```

The password checks reject a wrong password, nonexistent user, NONE and omitted
auth. `HIVE_TEST_ROLE=ADMIN` optionally selects the test administrator role for
LDAP fixture provisioning, if that account has the role. CUSTOM providers must
implement the standard Hive password-provider SPI and reject these invalid
credentials. `auth` omitted by the adapter defaults to PyHive's NONE; it does not
negotiate with the server. The password tests run only for LDAP/CUSTOM and are explicitly marked as skipped
for other authentication modes.

## Restart / stop / remove

```sh
docker compose stop inceptor
docker compose up -d inceptor
python manage.py init
# For a Kerberos-configured endpoint, reapply kerberos-enable, then kinit.
python manage.py wait
```

On an installed volume, `init` first resumes the normal vendor License daemon
(the Manager operation API needs it), then starts services in dependency order,
without regenerating configuration or reinstalling services. Use the same hostname
and Compose project so the vendor's service state still resolves to its original node.

`docker compose --profile kerberos down` removes this project's containers and
network while retaining its data volume and the local Kerberos state. A full
reset requires explicitly removing that project's data volume and `state/`
after stopping it; this deletes test data and keys. Do not reset licensing state
to extend an expired evaluation.

Unit checks for these helpers (from the workspace root):

```sh
python -m pytest datus-hive/tests/unit/test_inceptor_environment.py -q
```
