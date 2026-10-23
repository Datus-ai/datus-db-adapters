"""Helpers for the isolated Hive/Inceptor compatibility suite.

This module does not participate in the Dosi shared corpus. Exact-number
comparison deliberately avoids float coercion and text comparison keeps spaces.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import subprocess
import time
import uuid
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml


def load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def content_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def validate(root: Path) -> tuple[list[dict], list[dict]]:
    lock = load_json(root.with_suffix(".lock.json"))
    assert content_hash(root) == lock["content_sha256"], "HS2 fixture copy differs from its source lock"
    profiles = load(root / "profiles.yaml")
    suite = load(root / "suite.yaml")
    catalog = load(root / "capabilities.yaml")
    assert profiles["version"] == suite["version"] == catalog["version"] == 1
    native, metrics, ids, used = [], [], set(), set()
    for scenario in suite["scenarios"]:
        assert scenario and all(c.isascii() and (c.isalnum() or c == "_") for c in scenario)
        directory = root / "scenarios" / scenario
        for target in ("hive", "inceptor"):
            setup = load(directory / f"setup.{target}.yaml")
            assert setup["steps"]
            for step in setup["steps"]:
                assert set(step) == {"kind", "sql"} and step["kind"] in {"ddl", "insert", "external_alias"}
                render(step["sql"].replace("{{orders_location}}", "file:///validation/orders"), "validation")
        for entry in load(directory / "sql.yaml")["cases"]:
            assert set(entry) <= {
                "id",
                "capabilities",
                "targets",
                "sql",
                "expected",
                "expect_error",
                "expect_error_by_backend",
                "expected_by_profile",
                "formats",
            }
            assert ("expected" in entry) != ("expect_error" in entry)
            render(entry["sql"], "validation")
            for name, expected in entry.get("expected_by_profile", {}).items():
                assert name in profiles["profiles"]
                assert profiles["profiles"][name]["target"] in entry["targets"]
                validate_expected(expected)
            native.append({**entry, "scenario": scenario})
        path = directory / "metrics.yaml"
        if path.exists():
            definition = load(path)
            assert definition["model"] == "model.yaml"
            for entry in definition["cases"]:
                metrics.append({**entry, "scenario": scenario, "model": definition["model"]})
    for entry in native + metrics:
        assert entry["id"] not in ids and entry["id"]
        ids.add(entry["id"])
        assert entry["targets"] and set(entry["targets"]) <= {"hive", "inceptor"}
        assert entry["capabilities"]
        used.update(entry["capabilities"])
        if "expected" in entry:
            validate_expected(entry["expected"])
    assert used == set(catalog["capabilities"]), "unknown or uncovered HS2 capability"
    for capability in catalog["capabilities"].values():
        assert capability["required_cases"] and set(capability["required_cases"]) <= ids, "missing required HS2 case"
    return native, metrics


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_expected(expected: dict) -> None:
    assert set(expected) == {"columns", "rows", "types", "ordered"}
    assert expected["columns"] and len(expected["columns"]) == len(expected["types"])
    assert all(len(row) == len(expected["columns"]) for row in expected["rows"])
    assert set(expected["types"]) <= {"text", "int", "decimal", "bool", "date", "timestamp", "time", "binary", "json"}


def render(text: str, database: str) -> str:
    assert database and all(c.isascii() and (c.isalnum() or c == "_") for c in database)
    result = text.replace("{{db}}", f"`{database}`").replace("{{db_name}}", database)
    assert "{{" not in result, "Unknown HS2 fixture placeholder"
    return result


def canonical(value: Any, kind: str, *, csv_value: bool = False) -> Any:
    if value is None:
        return None
    if csv_value and value == "":
        return None  # CSV null/empty ambiguity is tested only through native rows.
    if kind == "decimal":
        return Decimal(str(value))
    if kind == "int":
        assert not isinstance(value, bool)
        if csv_value:
            return int(value)
        assert isinstance(value, int), f"Expected integer, got {type(value).__name__}: {value!r}"
        return value
    if kind == "bool":
        if csv_value:
            assert value.lower() in {"true", "false", "0", "1"}
            return value.lower() in {"true", "1"}
        assert isinstance(value, bool)
        return value
    if kind == "binary":
        if isinstance(value, (bytes, bytearray, memoryview)):
            return "base64:" + base64.b64encode(value).decode()
        return str(value)
    if kind == "json":
        parsed = json.loads(value) if isinstance(value, str) else value
        return json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if kind == "text":
        assert isinstance(value, str), f"Expected string, got {type(value).__name__}"
    text = str(value)
    if kind == "date":
        return text.removesuffix(" 00:00:00")
    if kind in {"timestamp", "time"}:
        text = text.replace("T", " ")
        return text.rstrip("0").rstrip(".") if "." in text else text
    return text


def compare(expected: dict, columns: list[str], rows: list[list], *, csv_values: bool = False) -> None:
    assert [c.casefold() for c in columns] == [c.casefold() for c in expected["columns"]]
    assert len(rows) == len(expected["rows"]), (rows, expected["rows"])

    def normalized(values: list[list], csv: bool) -> list[tuple]:
        assert all(len(row) == len(expected["types"]) for row in values)
        return [
            tuple(canonical(value, kind, csv_value=csv) for value, kind in zip(row, expected["types"], strict=True))
            for row in values
        ]

    wanted = normalized(expected["rows"], False)
    actual = normalized(rows, csv_values)
    if expected["ordered"]:
        assert actual == wanted, (actual, wanted)
    else:
        assert Counter(actual) == Counter(wanted), (actual, wanted)


def result_rows(payload: Any, result_format: str) -> tuple[list[str], list[list]]:
    if result_format == "csv":
        records = list(csv.reader(io.StringIO(payload)))
        return records[0], records[1:]
    if result_format == "pandas":
        return list(payload.columns), payload.values.tolist()
    if result_format == "arrow":
        return list(payload.column_names), [list(row.values()) for row in payload.to_pylist()]
    assert payload, "Use native column metadata to verify a zero-row result"
    return list(payload[0]), [list(row.values()) for row in payload]


def selected_cases(root: Path, kind: str) -> list[dict]:
    native, metrics = validate(root)
    name = os.getenv("HS2_TEST_PROFILE")
    if not name:
        return []
    profiles = load(root / "profiles.yaml")["profiles"]
    assert name in profiles, f"Unknown HS2_TEST_PROFILE {name}"
    target = profiles[name]["target"]
    cases = [c for c in (native if kind == "native" else metrics) if target in c["targets"]]
    assert cases, "HS2 profile selected zero cases"
    return cases


class Environment:
    """One explicitly selected profile, lazily provisioned owned databases."""

    def __init__(self, root: Path, backend: str):
        from datus_hive import HiveConfig, HiveConnector

        self.root, self.backend = root, backend
        validate(root)
        self.input_hash = content_hash(root)
        self.name = os.environ["HS2_TEST_PROFILE"]
        self.profile = load(root / "profiles.yaml")["profiles"][self.name]
        self.connection = load(Path(os.environ["DOSI_TEST_HIVE_CONNECTIONS"]))["datasources"][
            os.getenv("DOSI_TEST_HIVE_CONNECTION", "hive")
        ].copy()
        assert self.connection.get("server_type", "hive") == self.profile["target"], "HS2 profile/server_type mismatch"
        self.connection["configuration"] = {**self.connection.get("configuration", {}), **self.profile["configuration"]}
        allowed = set(HiveConfig.model_fields)
        config = {key: value for key, value in self.connection.items() if key in allowed}
        if "user" in self.connection and "username" not in config:
            config["username"] = self.connection["user"]
        config["database"] = "default"
        self.connector = HiveConnector(HiveConfig(**config))
        self.databases: dict[str, str] = {}
        self.loaded: set[str] = set()
        self.checks: list[dict] = []
        self.started = time.time_ns()
        try:
            version = self.query(self.profile["version_sql"])
            self.server_version = str(version.sql_return)
            assert self.profile["version_contains"] in self.server_version, self.server_version
            for key, value in self.profile["configuration"].items():
                with self.connector._ensure_engine().connect() as raw:
                    actual = str(raw.exec_driver_sql(f"SET {key}").fetchall())
                assert key in actual and value.lower() in actual.lower(), (key, actual)
        except BaseException:
            self.connector.close()
            raise

    def query(self, sql: str, result_format: str = "list") -> Any:
        result = self.connector.execute_query(sql, result_format=result_format)
        assert result.success, result.error
        return result

    def ensure(self, scenario: str) -> str:
        if scenario in self.databases:
            assert scenario in self.loaded, f"HS2 setup failed earlier for {scenario}"
            return self.databases[scenario]
        database = "hs2_compat_" + uuid.uuid4().hex[:16]
        result = self.connector.execute_ddl(f"CREATE DATABASE `{database}`")
        assert result.success, result.error
        self.databases[scenario] = database  # Cleanup includes partially loaded fixtures.
        setup = load(self.root / "scenarios" / scenario / f"setup.{self.profile['target']}.yaml")
        for step in setup["steps"]:
            sql = step["sql"]
            if step["kind"] == "external_alias":
                metadata = self.query(f"DESCRIBE FORMATTED `{database}`.orders").sql_return
                locations = [
                    list(row.values())[1].strip()
                    for row in metadata
                    if str(list(row.values())[0]).strip().rstrip(":").lower() == "location"
                ]
                assert len(locations) == 1 and database in locations[0] and "'" not in locations[0]
                sql = sql.replace("{{orders_location}}", locations[0])
            method = self.connector.execute_insert if step["kind"] == "insert" else self.connector.execute_ddl
            result = method(render(sql, database))
            assert result.success, result.error
        self.loaded.add(scenario)
        return database

    def native(self, case: dict, result_format: str) -> None:
        outcome = {"case": case["id"], "check": result_format, "status": "failed"}
        self.checks.append(outcome)
        database = self.ensure(case["scenario"])
        result = self.connector.execute_query(render(case["sql"], database), result_format=result_format)
        if "expect_error" in case:
            assert (
                not result.success
                and case.get("expect_error_by_backend", {}).get(self.backend, case["expect_error"]) in result.error
            ), result.error
        else:
            assert result.success, result.error
            expected = case.get("expected_by_profile", {}).get(self.name, case["expected"])
            columns, rows = result_rows(result.sql_return, result_format)
            compare(expected, columns, rows, csv_values=result_format == "csv")
        outcome["status"] = "passed"

    def close(self) -> None:
        errors = []
        try:
            for database in self.databases.values():
                result = self.connector.execute_ddl(f"DROP DATABASE `{database}` CASCADE")
                if not result.success:
                    errors.append(result.error)
        finally:
            self.connector.close()
            try:
                revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip()
            except (OSError, subprocess.CalledProcessError):
                revision = "unknown"
            directory = Path(os.getenv("HS2_TEST_REPORT_DIR", ".test-output/hs2"))
            directory.mkdir(parents=True, exist_ok=True)
            report = {
                "backend": self.backend,
                "profile": self.name,
                "server_version": self.server_version,
                "configuration": self.profile["configuration"],
                "fixture_hash": self.input_hash,
                "code_revision": revision,
                "code_dirty": bool(
                    subprocess.check_output(["git", "status", "--porcelain"], cwd=self.root, text=True).strip()
                )
                if revision != "unknown"
                else None,
                "databases": list(self.databases.values()),
                "checks": self.checks,
                "cleanup_errors": errors,
            }
            (directory / f"{self.backend}-{self.name}-{self.started}.json").write_text(
                json.dumps(report, indent=2), encoding="utf-8"
            )
        assert content_hash(self.root) == self.input_hash, "HS2 fixture changed during execution"
        assert not errors, errors
