#!/usr/bin/env python3
"""Run an isolated, verified-TLS database integration suite and clean up."""

import argparse
import os
import signal
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from ci.integration.generate_tls import generate

ROOT = Path(__file__).resolve().parents[1]


def run(adapter: str) -> None:
    prefix = adapter.upper()
    service, internal_port = {
        "mysql": ("mysql", "3306"),
        "postgresql": ("postgres", "5432"),
        "clickhouse": ("clickhouse", "8443"),
    }[adapter]
    project = f"datus-tls-{adapter}-{uuid.uuid4().hex[:8]}"
    with tempfile.TemporaryDirectory(prefix="datus-db-tls-") as directory:
        generate(Path(directory))
        env = dict(os.environ)
        env.update(
            {
                "DATUS_TEST_TLS_DIR": directory,
                f"{prefix}_HOST_PORT": "127.0.0.1:0",
                f"{prefix}_HOST": "localhost",
                f"{prefix}_USER": "test_user",
                f"{prefix}_PASSWORD": "test_password",
                f"{prefix}_DATABASE": "test",
                f"DATUS_TEST_{prefix}_USER": "test_user",
                f"DATUS_TEST_{prefix}_PASSWORD": "test_password",
                f"DATUS_TEST_{prefix}_DATABASE": "test",
            }
        )
        if adapter == "mysql":
            env["DATUS_TEST_MYSQL_ROOT_PASSWORD"] = "test_password"
            env["MYSQL_SSL_CA"] = f"{directory}/ca.crt"
        elif adapter == "postgresql":
            env["POSTGRESQL_SCHEMA"] = "public"
            env["POSTGRESQL_SSLROOTCERT"] = f"{directory}/ca.crt"
        else:
            env.update(
                {
                    "CLICKHOUSE_HTTPS_HOST_PORT": "127.0.0.1:0",
                    "CLICKHOUSE_HTTP_HOST_PORT": "127.0.0.1:0",
                    "CLICKHOUSE_PROTOCOL": "https",
                    "CLICKHOUSE_VERIFY": "true",
                    "CLICKHOUSE_CA_CERT": f"{directory}/ca.crt",
                    "CLICKHOUSE_REQUIRED": "1",
                }
            )
        compose = [
            "docker",
            "compose",
            "-p",
            project,
            "-f",
            f"datus-{adapter}/docker-compose.yml",
            "-f",
            f"datus-{adapter}/docker-compose.tls.yml",
        ]

        def command(args, **options):
            return subprocess.run(args, cwd=ROOT, env=env, check=True, **options)

        try:
            command([*compose, "up", "-d", "--wait", "--wait-timeout", "180"])
            address = command([*compose, "port", service, internal_port], capture_output=True, text=True).stdout.strip()
            env[f"{prefix}_PORT"] = address.rsplit(":", 1)[1]
            if adapter == "clickhouse":
                plain = command([*compose, "port", service, "8123"], capture_output=True, text=True).stdout.strip()
                env["CLICKHOUSE_PLAIN_PORT"] = plain.rsplit(":", 1)[1]
            command(
                ["uv", "run", "--package", f"datus-{adapter}", "python", "-m", f"ci.integration.readiness.{adapter}"]
            )
            command(
                [
                    "uv",
                    "run",
                    "--package",
                    f"datus-{adapter}",
                    "--with",
                    "pytest",
                    "python",
                    "-m",
                    "pytest",
                    f"datus-{adapter}/tests/integration",
                    "-m",
                    "integration",
                    "--strict-markers",
                    "--tb=short",
                    f"--ignore=datus-{adapter}/tests/integration/test_aurora_cloud.py",
                ]
            )
        except subprocess.CalledProcessError:
            subprocess.run([*compose, "logs", "--no-color", "--tail=60"], cwd=ROOT, env=env, check=False)
            raise
        finally:
            command([*compose, "down", "-v", "--remove-orphans"])


if __name__ == "__main__":

    def interrupted(signum, frame):
        # Let the finally block remove this invocation's project on CI cancellation.
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("adapter", choices=("mysql", "postgresql", "clickhouse"))
    args = parser.parse_args()
    try:
        run(args.adapter)
    except KeyboardInterrupt:
        sys.exit(130)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"TLS integration failed: {exc}", file=sys.stderr)
        sys.exit(1)
