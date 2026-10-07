"""Tests for scripts/validate_metadata.py.

A validator that cannot fail is worse than none: it produces a green check and
false confidence. So each check is exercised against a deliberately broken copy
of the artifact, not just against the real one.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]

# Load the script as a module; scripts/ is deliberately not an installed package.
_spec = importlib.util.spec_from_file_location(
    "validate_metadata", PACKAGE_ROOT / "scripts" / "validate_metadata.py"
)
assert _spec and _spec.loader
validate_metadata = importlib.util.module_from_spec(_spec)
sys.modules["validate_metadata"] = validate_metadata
_spec.loader.exec_module(validate_metadata)


# A minimal valid server.json, used as the base for corruption tests.
VALID_SERVER_JSON = {
    "name": "io.github.example/thing",
    "description": "A short description.",
    "version": "1.0.0",
    "packages": [
        {
            "registryType": "pypi",
            "identifier": "thing",
            "version": "1.0.0",
            "runtimeHint": "uvx",
            "transport": {"type": "stdio"},
        }
    ],
}


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the validator at a throwaway package root."""
    monkeypatch.setattr(validate_metadata, "PACKAGE_ROOT", tmp_path)
    (tmp_path / "skills" / "a-skill").mkdir(parents=True)
    (tmp_path / "skills" / "a-skill" / "SKILL.md").write_text("---\nname: a-skill\n---\n")
    return tmp_path


def _write(root: Path, document: dict) -> None:
    (root / "server.json").write_text(json.dumps(document))


def _errors(root: Path) -> list[str]:
    problems = validate_metadata.Problems()
    # The cache path is passed so no network access is attempted.
    validate_metadata.check_server_json(problems, cache=root / "no-such-cache.json")
    return list(problems)


def test_the_real_metadata_passes() -> None:
    """The committed artifacts must satisfy their own validator."""
    problems = validate_metadata.Problems()
    validate_metadata.check_server_json(problems, cache=Path("/tmp/nonexistent-schema.json"))
    validate_metadata.check_readme_ownership_marker(problems)
    assert not problems, f"the committed metadata has problems: {problems}"


def test_a_valid_document_produces_no_errors(sandbox: Path) -> None:
    _write(sandbox, VALID_SERVER_JSON)
    assert _errors(sandbox) == []


def test_an_over_long_description_is_caught(sandbox: Path) -> None:
    document = dict(VALID_SERVER_JSON, description="x" * 101)
    _write(sandbox, document)
    assert any("registry limit is 100" in problem for problem in _errors(sandbox))


def test_a_name_without_exactly_one_slash_is_caught(sandbox: Path) -> None:
    _write(sandbox, dict(VALID_SERVER_JSON, name="no-slash-here"))
    assert any("reverse-DNS" in problem for problem in _errors(sandbox))

    _write(sandbox, dict(VALID_SERVER_JSON, name="a/b/c"))
    assert any("reverse-DNS" in problem for problem in _errors(sandbox))


def test_a_version_range_is_caught(sandbox: Path) -> None:
    _write(sandbox, dict(VALID_SERVER_JSON, version="^1.0.0"))
    assert any("range" in problem for problem in _errors(sandbox))


def test_the_literal_latest_version_is_caught(sandbox: Path) -> None:
    _write(sandbox, dict(VALID_SERVER_JSON, version="latest"))
    assert any("'latest'" in problem for problem in _errors(sandbox))


def test_a_server_with_no_packages_or_remotes_is_caught(sandbox: Path) -> None:
    document = {k: v for k, v in VALID_SERVER_JSON.items() if k != "packages"}
    _write(sandbox, document)
    assert any("not installable" in problem for problem in _errors(sandbox))


def test_a_package_version_mismatch_is_caught(sandbox: Path) -> None:
    document = json.loads(json.dumps(VALID_SERVER_JSON))
    document["packages"][0]["version"] = "2.0.0"
    _write(sandbox, document)
    assert any("does not match the server version" in problem for problem in _errors(sandbox))


def test_an_unknown_registry_type_is_caught(sandbox: Path) -> None:
    document = json.loads(json.dumps(VALID_SERVER_JSON))
    document["packages"][0]["registryType"] = "floppy-disk"
    _write(sandbox, document)
    assert any("unknown registryType" in problem for problem in _errors(sandbox))


def test_an_unknown_transport_is_caught(sandbox: Path) -> None:
    document = json.loads(json.dumps(VALID_SERVER_JSON))
    document["packages"][0]["transport"] = {"type": "carrier-pigeon"}
    _write(sandbox, document)
    assert any("not a known transport" in problem for problem in _errors(sandbox))


def test_oversized_meta_is_caught(sandbox: Path) -> None:
    document = dict(VALID_SERVER_JSON, _meta={"x": "y" * 5000})
    _write(sandbox, document)
    assert any("_meta is" in problem for problem in _errors(sandbox))


def test_a_malformed_server_json_is_caught(sandbox: Path) -> None:
    (sandbox / "server.json").write_text("{ not json")
    assert any("invalid JSON" in problem for problem in _errors(sandbox))


def test_a_missing_server_json_is_caught(sandbox: Path) -> None:
    assert any("missing" in problem for problem in _errors(sandbox))


def test_a_missing_readme_marker_is_caught(sandbox: Path) -> None:
    _write(sandbox, VALID_SERVER_JSON)
    (sandbox / "README.md").write_text("# no marker here\n")
    problems = validate_metadata.Problems()
    validate_metadata.check_readme_ownership_marker(problems)
    assert any("ownership marker" in problem for problem in problems)


def test_a_present_readme_marker_passes(sandbox: Path) -> None:
    _write(sandbox, VALID_SERVER_JSON)
    (sandbox / "README.md").write_text("<!-- mcp-name: io.github.example/thing -->\n")
    problems = validate_metadata.Problems()
    validate_metadata.check_readme_ownership_marker(problems)
    assert not problems


def test_a_wheel_missing_its_skills_is_caught(sandbox: Path) -> None:
    """`force-include` could stop working without anything else noticing."""
    dist = sandbox / "dist"
    dist.mkdir()
    pkg = validate_metadata.WHEEL_PACKAGE_DIR
    wheel = dist / "thing-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(f"{pkg}/__init__.py", "")
        archive.writestr(f"{pkg}/plugin/.mcp.json", "{}")
        # The skill files are deliberately omitted.

    problems = validate_metadata.Problems()
    validate_metadata.check_wheel_contents(problems, dist_dir=dist)
    assert any("a-skill/SKILL.md" in problem for problem in problems)


def test_a_complete_wheel_passes(sandbox: Path) -> None:
    dist = sandbox / "dist"
    dist.mkdir()
    pkg = validate_metadata.WHEEL_PACKAGE_DIR
    wheel = dist / "thing-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(f"{pkg}/plugin/.claude-plugin/plugin.json", "{}")
        archive.writestr(f"{pkg}/plugin/.mcp.json", "{}")
        archive.writestr(f"{pkg}/plugin/skills/a-skill/SKILL.md", "---\nname: a-skill\n---\n")

    problems = validate_metadata.Problems()
    validate_metadata.check_wheel_contents(problems, dist_dir=dist)
    assert not problems


def test_a_missing_wheel_is_a_warning_not_a_failure(sandbox: Path) -> None:
    """`--skip-wheel` and a fresh checkout must both be usable."""
    problems = validate_metadata.Problems()
    validate_metadata.check_wheel_contents(problems, dist_dir=sandbox / "dist")
    assert not problems


def test_problems_report_exit_codes() -> None:
    clean = validate_metadata.Problems()
    assert clean.report() == 0

    dirty = validate_metadata.Problems()
    dirty.add("server.json", "something is wrong")
    assert dirty.report() == 1
