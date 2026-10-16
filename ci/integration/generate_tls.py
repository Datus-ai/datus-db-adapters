#!/usr/bin/env python3
"""Generate disposable localhost certificates for the MySQL/PostgreSQL TLS overlays."""

import argparse
import os
import subprocess
from pathlib import Path


def generate(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise ValueError("Use an empty directory for disposable TLS certificates")
    directory.chmod(0o755)

    def openssl(*args):
        subprocess.run(["openssl", *args], cwd=directory, check=True, capture_output=True)

    for name in ("ca", "wrong-ca"):
        openssl(
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-sha256",
            "-days",
            "2",
            "-subj",
            f"/CN=Datus disposable {name}",
            "-keyout",
            f"{name}.key",
            "-out",
            f"{name}.crt",
        )
    openssl(
        "req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=localhost", "-keyout", "server.key", "-out", "server.csr"
    )
    (directory / "server.ext").write_text("subjectAltName=DNS:localhost\nextendedKeyUsage=serverAuth\n")
    openssl(
        "x509",
        "-req",
        "-in",
        "server.csr",
        "-CA",
        "ca.crt",
        "-CAkey",
        "ca.key",
        "-CAcreateserial",
        "-days",
        "2",
        "-sha256",
        "-extfile",
        "server.ext",
        "-out",
        "server.crt",
    )
    for path in directory.iterdir():
        # The root entrypoint copies the server key and sets driver-specific ownership.
        path.chmod(0o600 if path.suffix == ".key" else 0o644)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    previous_umask = os.umask(0o077)
    try:
        generate(args.directory.resolve())
    finally:
        os.umask(previous_umask)
