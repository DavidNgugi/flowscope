#!/usr/bin/env python3
"""Validate the distribution metadata that is not covered by importing the code.

Three artifacts ship alongside the Python package, and a mistake in any of them
is invisible until a user's install or a registry publish fails. This script
checks them against their real constraints, and is run both by CI and by the
pytest suite, so there is one implementation rather than two that can drift.

    python scripts/validate_metadata.py

Exits non-zero and prints one ``::error::`` line per problem, which GitHub
Actions turns into an annotation.

Checks:

* ``server.json`` against the official MCP Registry JSON Schema (fetched, with a
  bundled fallback for offline use) plus the limits that schema does not express
  as constraints, such as the namespace/version rules.
* The built wheel actually contains the skills and plugin manifests, which
  ``force-include`` could silently stop doing.
"""

from __future__ import annotations

import argparse
import glob
import json
import urllib.request
import zipfile
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]

REGISTRY_SCHEMA_URL = (
    "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
)

# Constraints the published schema does not enforce as JSON Schema keywords,
# transcribed from the registry documentation.
DESCRIPTION_MAX = 100
NAME_MIN, NAME_MAX = 3, 200
META_MAX_BYTES = 4096
VERSION_RANGE_CHARS = set("^~*<>| ")

# Where `force-include` places the plugin payload inside the wheel.
WHEEL_PACKAGE_DIR = "flowscope_mcp"


class Problems(list):
    """A list of problems that prints itself in CI-annotated form."""

    def add(self, location: str, message: str) -> None:
        self.append(f"{location}: {message}")

    def report(self) -> int:
        for problem in self:
            print(f"::error::{problem}")
        if not self:
            print("metadata OK")
        return 1 if self else 0


def _load_schema(cache: Path | None) -> dict | None:
    """Fetch the registry schema, falling back to a local cache when offline."""
    if cache and cache.exists():
        try:
            return json.loads(cache.read_text())
        except json.JSONDecodeError:
            pass
    try:
        with urllib.request.urlopen(REGISTRY_SCHEMA_URL, timeout=20) as response:
            schema = json.load(response)
    except Exception as exc:  # noqa: BLE001 - offline is an expected condition
        print(f"::warning::could not fetch the registry schema ({exc}); skipping schema check")
        return None
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(schema))
    return schema


def check_server_json(problems: Problems, *, cache: Path | None) -> None:
    path = PACKAGE_ROOT / "server.json"
    if not path.is_file():
        problems.add("server.json", "missing")
        return
    try:
        instance = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        problems.add("server.json", f"invalid JSON: {exc}")
        return

    for field in ("name", "description", "version"):
        if not instance.get(field):
            problems.add("server.json", f"required field {field!r} is missing or empty")

    name = instance.get("name", "")
    if not (NAME_MIN <= len(name) <= NAME_MAX):
        problems.add("server.json", f"name must be {NAME_MIN}-{NAME_MAX} characters, got {len(name)}")
    if name.count("/") != 1:
        problems.add("server.json", "name must be reverse-DNS with exactly one '/'")
    elif not all(part for part in name.split("/")):
        problems.add("server.json", "name must have a non-empty namespace and server part")

    description = instance.get("description", "")
    if len(description) > DESCRIPTION_MAX:
        problems.add(
            "server.json",
            f"description is {len(description)} characters; the registry limit is {DESCRIPTION_MAX}",
        )

    version = instance.get("version", "")
    if set(version) & VERSION_RANGE_CHARS:
        problems.add("server.json", f"version {version!r} looks like a range; exact versions only")
    if version == "latest":
        problems.add("server.json", "version must not be 'latest'")

    packages = instance.get("packages") or []
    remotes = instance.get("remotes") or []
    if not packages and not remotes:
        problems.add("server.json", "neither packages nor remotes: the server is not installable")

    for index, package in enumerate(packages):
        where = f"server.json packages[{index}]"
        if not package.get("registryType"):
            problems.add(where, "registryType is required")
        elif package["registryType"] not in {"npm", "pypi", "cargo", "oci", "nuget", "mcpb"}:
            problems.add(where, f"unknown registryType {package['registryType']!r}")
        if not package.get("identifier"):
            problems.add(where, "identifier is required")
        transport = package.get("transport") or {}
        if transport.get("type") not in {"stdio", "streamable-http", "sse"}:
            problems.add(where, f"transport.type {transport.get('type')!r} is not a known transport")
        package_version = package.get("version")
        if package_version and package_version != version:
            problems.add(
                where,
                f"version {package_version!r} does not match the server version {version!r}",
            )

    meta = json.dumps(instance.get("_meta", {})).encode()
    if len(meta) > META_MAX_BYTES:
        problems.add("server.json", f"_meta is {len(meta)} bytes; the limit is {META_MAX_BYTES}")

    schema = _load_schema(cache)
    if schema is None:
        return
    try:
        from jsonschema import Draft7Validator
    except ImportError:
        print("::warning::jsonschema is not installed; skipping the schema check")
        return

    for error in sorted(Draft7Validator(schema).iter_errors(instance), key=lambda e: list(e.path)):
        problems.add(f"server.json {'/'.join(map(str, error.path)) or '<root>'}", error.message)


def check_readme_ownership_marker(problems: Problems) -> None:
    """The registry verifies PyPI ownership by finding this exact string."""
    server_json = PACKAGE_ROOT / "server.json"
    readme = PACKAGE_ROOT / "README.md"
    if not (server_json.is_file() and readme.is_file()):
        return
    name = json.loads(server_json.read_text()).get("name", "")
    marker = f"mcp-name: {name}"
    if marker not in readme.read_text():
        problems.add("README.md", f"missing the PyPI ownership marker {marker!r}")


def check_wheel_contents(problems: Problems, *, dist_dir: Path) -> None:
    """A wheel that lost the skills would still install, and silently ship less."""
    wheels = sorted(glob.glob(str(dist_dir / "*.whl")))
    if not wheels:
        print(f"::warning::no wheel in {dist_dir}; skipping the wheel contents check")
        return
    wheel = wheels[0]
    names = set(zipfile.ZipFile(wheel).namelist())
    required = {
        f"{WHEEL_PACKAGE_DIR}/plugin/.claude-plugin/plugin.json",
        f"{WHEEL_PACKAGE_DIR}/plugin/.mcp.json",
    }
    for skill in sorted((PACKAGE_ROOT / "skills").glob("*/SKILL.md")):
        required.add(f"{WHEEL_PACKAGE_DIR}/plugin/skills/{skill.parent.name}/SKILL.md")

    for name in sorted(required):
        if name not in names:
            problems.add(wheel, f"is missing {name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=PACKAGE_ROOT / "dist",
        help="Directory holding a built wheel to inspect, if one exists.",
    )
    parser.add_argument(
        "--schema-cache",
        type=Path,
        default=Path("/tmp/mcp-server.schema.json"),
        help="Where to cache the fetched registry schema between runs.",
    )
    parser.add_argument(
        "--skip-wheel",
        action="store_true",
        help="Do not require a built wheel (the default when none exists).",
    )
    args = parser.parse_args(argv)

    problems = Problems()
    check_server_json(problems, cache=args.schema_cache)
    check_readme_ownership_marker(problems)
    if not args.skip_wheel:
        check_wheel_contents(problems, dist_dir=args.dist_dir)
    return problems.report()


if __name__ == "__main__":
    raise SystemExit(main())
