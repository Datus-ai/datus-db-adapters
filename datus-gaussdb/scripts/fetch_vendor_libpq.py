# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Populate datus_gaussdb/_vendor/<arch>/ with the openGauss libpq binaries.

Extracts libpq + OpenSSL from the pinned openGauss container image for one or
both architectures. Used by wheel release builds and by developers running
integration tests from a source checkout.

Usage:
    python scripts/fetch_vendor_libpq.py [--arch x86_64|aarch64|all]
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

IMAGE = "opengauss/opengauss:7.0.0-RC2.B015"
LIBS = ("libpq.so.5.5", "libpq.so.5", "libssl.so.1.1", "libcrypto.so.1.1")
CONTAINER_LIB_DIR = "/usr/local/opengauss/lib"
VENDOR_DIR = Path(__file__).resolve().parent.parent / "datus_gaussdb" / "_vendor"
PLATFORMS = {"x86_64": "linux/amd64", "aarch64": "linux/arm64"}

MULAN_NOTICE = """The bundled libpq binaries are built from the openGauss project
(https://gitee.com/opengauss/openGauss-server) and are redistributed
under the Mulan Permissive Software License, Version 2 (MulanPSL-2.0):
http://license.coscl.org.cn/MulanPSL2
"""

OPENSSL_NOTICE = """The bundled libssl and libcrypto binaries are OpenSSL 1.1.x builds
shipped with the openGauss distribution. OpenSSL 1.1.x is redistributed
under the dual OpenSSL and SSLeay licenses:
https://www.openssl.org/source/license-openssl-ssleay.txt

This product includes software developed by the OpenSSL Project for use
in the OpenSSL Toolkit (http://www.openssl.org/). This product includes
cryptographic software written by Eric Young (eay@cryptsoft.com).
"""


def run(cmd: list) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)


def fetch(arch: str) -> None:
    platform = PLATFORMS[arch]
    target = VENDOR_DIR / arch
    target.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        container = f"datus-gaussdb-vendor-{arch}"
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
        run(["docker", "create", "--platform", platform, "--name", container, IMAGE])
        try:
            for lib in LIBS:
                run(["docker", "cp", f"{container}:{CONTAINER_LIB_DIR}/{lib}", str(Path(tmp) / lib)])
                (target / lib).write_bytes((Path(tmp) / lib).read_bytes())
        finally:
            subprocess.run(["docker", "rm", "-f", container], capture_output=True)

    (target / "MulanPSL-2.0.txt").write_text(MULAN_NOTICE)
    (target / "OPENSSL-LICENSE.txt").write_text(OPENSSL_NOTICE)
    print(f"Vendored {len(LIBS)} libraries into {target}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=[*PLATFORMS, "all"], default="all")
    args = parser.parse_args()
    arches = list(PLATFORMS) if args.arch == "all" else [args.arch]
    for arch in arches:
        fetch(arch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
