#!/usr/bin/env python3
"""Run an isolated, verified-TLS MySQL/PostgreSQL integration suite and clean up."""

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
    service, internal_port = ("mysql", "3306") if adapter == "mysql" else ("postgres", "5432")
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
                f"{prefix}_SSL_CA" if adapter == "mysql" else "POSTGRESQL_SSLROOTCERT": f"{directory}/ca.crt",
            }
        )
        if adapter == "mysql":
            env["DATUS_TEST_MYSQL_ROOT_PASSWORD"] = "test_password"
        else:
            env["POSTGRESQL_SCHEMA"] = "public"
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
            command(["uv", "run", "--all-packages", "python", "-m", f"ci.integration.readiness.{adapter}"])
            command(
                [
                    "uv",
                    "run",
                    "--all-packages",
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
    parser.add_argument("adapter", choices=("mysql", "postgresql"))
    args = parser.parse_args()
    try:
        run(args.adapter)
    except KeyboardInterrupt:
        sys.exit(130)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"TLS integration failed: {exc}", file=sys.stderr)
        sys.exit(1)
