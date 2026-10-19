"""Check a Linux CI host and stage Express's private bootstrap password file."""

import argparse
import json
import os
import platform
import re
import shutil
from pathlib import Path


def check_host(root: Path, *, skip_sysctl_check: bool = False):
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise RuntimeError("Express requires a prepared Linux x86_64 test host")
    if not skip_sysctl_check:
        for key, required in (("vm/max_map_count", 135217728), ("fs/aio-max-nr", 262144)):
            current = int((Path("/proc/sys") / key).read_text())
            if current < required:
                raise RuntimeError(f"Host prerequisite {key}={current}; review SAP requirements (at least {required})")
    # SAP documents 8 GB for server-only. Headroom/disk are adjustable trial budgets,
    # not a measured footprint or a disk reservation/quota.
    memory_limit_gib = int(os.getenv("HANA_MEMORY_LIMIT_GIB", "8"))
    minimum_disk_gib = int(os.getenv("HANA_MIN_FREE_DISK_GIB", "12"))
    if memory_limit_gib < 8:
        raise ValueError("HANA_MEMORY_LIMIT_GIB must be at least 8 for the server-only test instance")
    if minimum_disk_gib < 1:
        raise ValueError("HANA_MIN_FREE_DISK_GIB must be positive")
    state = root / ".express"
    free_gib = shutil.disk_usage(state if state.exists() else root).free / 1024**3
    available_kib = next(
        int(line.split()[1])
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith("MemAvailable:")
    )
    if free_gib < minimum_disk_gib:
        raise RuntimeError(f"Free disk {free_gib:.1f} GiB is below the configured trial budget {minimum_disk_gib} GiB")
    available_gib = available_kib / 1024**2
    required_memory_gib = memory_limit_gib + 2
    if available_gib < required_memory_gib:
        raise RuntimeError(
            f"Available RAM {available_gib:.1f} GiB is below {required_memory_gib} GiB "
            f"({memory_limit_gib} GiB container limit plus 2 GiB host headroom)"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--cleanup", action="store_true", help="Remove this checkout's stopped Express test state")
    parser.add_argument(
        "--skip-sysctl-check",
        action="store_true",
        help="Trial with existing host sysctls; pair with docker-compose.skip-sysctl.yml",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.cleanup:
        cleanup_express(root)
        return
    check_host(root, skip_sysctl_check=args.skip_sysctl_check)
    if args.check_only:
        return
    password = os.environ.get("DATUS_TEST_HANA_PASSWORD", "")
    if (
        len(password) < 8
        or not any(c.isupper() for c in password)
        or not any(c.islower() for c in password)
        or not any(c.isdigit() for c in password)
    ):
        raise ValueError("DATUS_TEST_HANA_PASSWORD must meet SAP password requirements")
    if os.geteuid() != 0:
        raise RuntimeError(
            "Stage the password file with sudo, preserving DATUS_TEST_HANA_PASSWORD; ownership must be 12000:79"
        )
    if any(char in password for char in "`$\\'\""):
        raise ValueError("Bootstrap password contains a character prohibited by the Express image")
    mount = root / ".express"
    if mount.is_symlink():
        raise RuntimeError("Refusing a symlink Express mount directory")
    mount.mkdir(exist_ok=True, mode=0o700)
    os.chown(mount, 12000, 79)
    path = mount / "passwords.json"
    if path.is_symlink():
        raise RuntimeError("Refusing a symlink password file")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump({"master_password": password}, handle)
    os.chmod(path, 0o600)
    os.chown(path, 12000, 79)


def cleanup_express(root: Path):
    mount = root / ".express"
    if mount.is_symlink() or mount.is_mount():
        raise RuntimeError("Refusing to clean a symlink or mounted Express directory")
    # is_mount() alone misses same-filesystem bind mounts on Linux. Also avoid
    # descending into any separately mounted data/log subdirectory.
    try:
        mountinfo = Path("/proc/self/mountinfo").read_text()
    except FileNotFoundError:
        mountinfo = ""
    target = str(mount.resolve())
    for line in mountinfo.splitlines():
        fields = line.split()
        if len(fields) < 5:
            continue
        location = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), fields[4])
        if location == target or location.startswith(target + "/"):
            raise RuntimeError("Refusing to clean a mounted Express directory or its mounted descendants")
    if mount.exists():
        shutil.rmtree(mount)


if __name__ == "__main__":
    main()
