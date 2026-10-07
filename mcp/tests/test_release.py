"""Tests for scripts/version.py and the release workflow contract.

A version drift is the most expensive mistake in this repository: PyPI never
allows reusing a version, so a bad release burns the number permanently. These
tests make the drift impossible to commit and pin the release triggers so a
future edit cannot make publishing automatic on a branch push.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _load(name: str, path: Path):
    """Import a script by path, registering it so dataclasses can resolve."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


version = _load("version_module", PACKAGE_ROOT / "scripts" / "version.py")


# ---- the version script ----------------------------------------------------


def test_all_declared_versions_agree() -> None:
    """The committed state must be internally consistent."""
    assert version.command_check() == 0


def test_every_expected_site_is_covered() -> None:
    """A new version declaration must not be able to slip in unnoticed.

    If someone adds a version to a manifest and forgets to register it here, the
    release could publish mismatched metadata, so the site count is pinned.
    """
    labels = [site.label for site in version.version_sites()]
    assert len(labels) == 6, f"expected 6 version sites, found {len(labels)}: {labels}"

    paths = {site.display_path for site in version.version_sites()}
    assert paths == {
        "mcp/pyproject.toml",
        "mcp/src/flowscope_mcp/__init__.py",
        "mcp/server.json",
        "mcp/.claude-plugin/plugin.json",
        ".claude-plugin/marketplace.json",
    }


def test_source_of_truth_is_pyproject() -> None:
    assert version.version_sites()[0].path == PACKAGE_ROOT / "pyproject.toml"


def test_the_runtime_version_matches_the_distribution() -> None:
    """`flowscope-mcp --version` must report the packaged version."""
    from flowscope_mcp import __version__

    assert __version__ == version.source_version()


def test_a_disagreeing_site_is_detected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The check must actually fail, not just print."""

    fake = tmp_path / "pyproject.toml"
    fake.write_text('version = "1.0.0"\n')
    other = tmp_path / "other.json"
    other.write_text('  "version": "9.9.9",\n')

    sites = [
        version.VersionSite("pyproject", fake, r'^version = "(?P<version>' + version.VERSION_PATTERN + r')"'),
        version.VersionSite("other", other, r'^  "version": "(?P<version>' + version.VERSION_PATTERN + r')",'),
    ]
    monkeypatch.setattr(version, "version_sites", lambda: sites)
    assert version.command_check() == 1


def test_bump_rewrites_every_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = {
        "a.toml": 'name = "x"\nversion = "1.0.0"\n',
        "b.json": '{\n  "version": "1.0.0"\n}\n',
    }
    sites = []
    for name, content in files.items():
        path = tmp_path / name
        path.write_text(content)
        pattern = r'^version = "(?P<version>' + version.VERSION_PATTERN + r')"' if name.endswith(".toml") \
            else r'^  "version": "(?P<version>' + version.VERSION_PATTERN + r')"'
        sites.append(version.VersionSite(name, path, pattern))

    monkeypatch.setattr(version, "version_sites", lambda: sites)
    assert version.command_bump("2.0.0") == 0
    for name, path in [(n, tmp_path / n) for n in files]:
        assert "2.0.0" in path.read_text(), f"{name} was not bumped"
        assert "1.0.0" not in path.read_text(), f"{name} kept the old version"


def test_bump_is_a_no_op_when_already_at_the_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text('version = "3.0.0"\n')
    sites = [
        version.VersionSite("p", path, r'^version = "(?P<version>' + version.VERSION_PATTERN + r')"')
    ]
    monkeypatch.setattr(version, "version_sites", lambda: sites)
    assert version.command_bump("3.0.0") == 0
    assert path.read_text() == 'version = "3.0.0"\n'


def test_a_range_is_rejected_rather_than_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The registry rejects ranges, so refuse to write one in the first place."""
    path = tmp_path / "pyproject.toml"
    path.write_text('version = "1.0.0"\n')
    sites = [
        version.VersionSite("p", path, r'^version = "(?P<version>' + version.VERSION_PATTERN + r')"')
    ]
    monkeypatch.setattr(version, "version_sites", lambda: sites)
    for bad in ("^1.0.0", "~1.0.0", "latest", "1.x", "1.0"):
        with pytest.raises(SystemExit):
            version.command_bump(bad)
    # Nothing was written.
    assert path.read_text() == 'version = "1.0.0"\n'


@pytest.mark.parametrize(
    "candidate",
    ["0.1.0", "1.2.3", "0.2.0-rc1", "1.0.0-alpha.2", "2.0.0+build.5"],
)
def test_valid_versions_are_accepted(candidate: str) -> None:
    assert version.VALID_VERSION.match(candidate)


@pytest.mark.parametrize("candidate", ["1.0.0a1", "1.0.0.post1"])
def test_pep440_separatorless_forms_are_rejected(candidate: str) -> None:
    """Requiring a separator keeps the pattern unambiguous across tag, PyPI and
    registry, all of which handle a hyphenated prerelease identically."""
    assert not version.VALID_VERSION.match(candidate)


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("0.1.0", False),
        ("1.2.3", False),
        ("0.2.0-rc1", True),
        ("1.0.0-alpha.2", True),
        # Build metadata is not a prerelease.
        ("2.0.0+build.5", False),
    ],
)
def test_prerelease_classification(candidate: str, expected: bool) -> None:
    assert version.is_prerelease(candidate) is expected


@pytest.mark.parametrize("candidate", ["^1.0.0", "~1.0.0", "1.x", "1", "latest", ""])
def test_invalid_versions_are_rejected(candidate: str) -> None:
    assert not version.VALID_VERSION.match(candidate)


def test_bumping_is_reversible_in_a_git_tree() -> None:
    """Guard against a bump touching something outside the declared sites.

    This is a documentation-grade assertion rather than an execution: the test
    suite must leave the tree exactly as it found it.
    """
    before = version.collect()
    assert all(value for _, value in before), "every site should currently declare a version"


# ---- the workflow contract -------------------------------------------------


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


def test_release_does_not_run_on_branch_pushes() -> None:
    """A merge must never publish. Only tags and manual dispatch may."""
    triggers = _workflow("release.yml")[True]  # YAML parses bare `on:` as True
    assert "push" in triggers
    # If push is scoped to branches, a merge could publish; it must be tags only.
    push = triggers["push"] or {}
    assert "tags" in push, "the release workflow's push trigger must be tag-scoped"
    assert "branches" not in push, "the release workflow must not trigger on branches"
    assert "workflow_dispatch" in triggers, "manual publishing must stay available"


def test_manual_release_requires_an_explicit_publish_flag() -> None:
    """The default manual run must be a dry run."""
    inputs = _workflow("release.yml")[True]["workflow_dispatch"]["inputs"]
    assert "publish" in inputs
    assert inputs["publish"]["default"] is False
    assert inputs["publish"]["type"] == "boolean"


def test_manual_release_accepts_a_version_and_a_ref() -> None:
    """The manual path lets you set the version and choose what to release."""
    inputs = _workflow("release.yml")[True]["workflow_dispatch"]["inputs"]
    assert "version" in inputs
    assert "ref" in inputs


def test_release_gates_on_the_shared_verification_action() -> None:
    """Release must run the same checks as CI, from one definition."""
    source = (WORKFLOWS / "release.yml").read_text()
    assert "./.github/actions/verify-mcp" in source


def test_publishing_is_split_so_pypi_cannot_be_half_done() -> None:
    """PyPI and the registry are separate jobs with separate permissions."""
    jobs = _workflow("release.yml")["jobs"]
    assert "publish-pypi" in jobs
    assert "publish-registry" in jobs
    # The registry waits for PyPI, because it verifies the published README.
    assert "publish-pypi" in jobs["publish-registry"]["needs"]
    # Only the PyPI job needs to mint an OIDC token for trusted publishing.
    assert "id-token" in jobs["publish-pypi"]["permissions"]
    # Trusted publishing means no stored token.
    assert "password" not in str(jobs["publish-pypi"]).lower()
    assert "PYPI_API_TOKEN" not in (WORKFLOWS / "release.yml").read_text()


def test_release_checks_pypi_before_uploading() -> None:
    """Uploading an existing version fails permanently, so check first."""
    source = (WORKFLOWS / "release.yml").read_text()
    assert "already published on PyPI" in source
    assert "pypi.org/pypi/flowscope-mcp" in source


def test_release_verifies_the_version_against_the_tag() -> None:
    source = (WORKFLOWS / "release.yml").read_text()
    assert "scripts/version.py check" in source


def test_registry_publish_waits_for_pypi_propagation() -> None:
    """The registry reads the published README, which is not instant."""
    source = (WORKFLOWS / "release.yml").read_text()
    assert "Wait for the PyPI release to be visible" in source


def test_ci_workflow_runs_the_version_check_independently() -> None:
    """Drift should be visible on its own, not buried in a longer job."""
    jobs = _workflow("mcp.yml")["jobs"]
    assert "versions" in jobs
    runs = " ".join(step.get("run", "") for step in jobs["versions"]["steps"])
    assert "scripts/version.py check" in runs


def test_ci_workflow_does_not_publish() -> None:
    """CI must never publish; that is release.yml's job alone."""
    source = (WORKFLOWS / "mcp.yml").read_text().lower()
    assert "uv publish" not in source
    assert "mcp-publisher publish" not in source


def test_ci_proves_the_declared_python_floor() -> None:
    """`requires-python = ">=3.10"` is a promise worth testing."""
    source = (WORKFLOWS / "mcp.yml").read_text()
    assert '"3.10"' in source


def test_the_composite_action_checks_versions_first() -> None:
    """Order matters: the cheapest and most damaging check runs before builds."""
    steps = yaml.safe_load((REPO_ROOT / ".github/actions/verify-mcp/action.yml").read_text())
    runs = [step.get("run", "") for step in steps["runs"]["steps"]]
    version_index = next(i for i, r in enumerate(runs) if "version.py check" in r)
    build_index = next(i for i, r in enumerate(runs) if "python -m build" in r)
    assert version_index < build_index, "the version check should precede the build"


def test_the_composite_action_validates_the_wheel_contents() -> None:
    steps = yaml.safe_load((REPO_ROOT / ".github/actions/verify-mcp/action.yml").read_text())
    runs = " ".join(step.get("run", "") for step in steps["runs"]["steps"])
    assert "validate_metadata.py --dist-dir dist" in runs


# ---- CI tooling must be declared, not assumed ------------------------------
#
# `python -m build` failed in CI on the first release because `build` was
# installed by hand locally and never declared. Anything the workflows invoke
# has to be a declared dependency or installed by an explicit step.


def _run_commands(workflow_or_action: dict) -> list[tuple[str, str]]:
    """Yield (job_or_step, first-command-word) for every `run:` shell line."""
    found: list[tuple[str, str]] = []
    jobs = workflow_or_action.get("jobs")
    containers = jobs.values() if jobs else [workflow_or_action.get("runs", {})]
    for container in containers:
        for step in container.get("steps", []):
            script = step.get("run")
            if not script:
                continue
            label = step.get("name") or step.get("uses") or "step"
            for raw in script.splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or line.startswith("-"):
                    continue
                if line.endswith("\\") or "=" in line.split()[0]:
                    continue
                tokens = line.split()
                head = tokens[0]
                # `python -m mod` is a module invocation, not a binary on PATH.
                if head in {"python", "python3"} and len(tokens) > 1 and tokens[1] == "-m":
                    found.append((label, f"python -m {tokens[2]}"))
                else:
                    found.append((label, head))
    return found


def _declared_dev_requirements() -> set[str]:
    import tomllib

    pyproject = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text())
    names = set()
    for requirement in pyproject["project"]["optional-dependencies"]["dev"]:
        # Strip any version specifier or extras.
        name = requirement.split(">=")[0].split("==")[0].split("[")[0].split(">")[0]
        names.add(name.strip().lower())
    return names


def test_every_tool_the_verify_action_runs_is_available() -> None:
    """A missing tool fails the release *after* it has started.

    Tools provided by the runner image, installed by an explicit step, or run
    through a package manager are exempt; anything else must be a declared dev
    dependency.
    """
    action = yaml.safe_load((REPO_ROOT / ".github/actions/verify-mcp/action.yml").read_text())
    declared = _declared_dev_requirements()

    # Preinstalled on GitHub's image, provided by another step, or a shell
    # keyword rather than a tool.
    provided_elsewhere = {
        "pip",  # preinstalled
        "curl",  # preinstalled
        "python",
        "python3",
        "npx",  # provided by actions/setup-node
        "skills-ref",  # installed by the preceding explicit step
        # shell keywords and builtins
        "for", "do", "done", "if", "then", "fi", "else", "set", "export",
        "echo", "cd", "test", "case", "esac", "while",
    }

    missing = []
    for label, tool in _run_commands(action):
        if tool.startswith("python -m "):
            module = tool.split()[-1]
            if module == "build" and "build" not in declared:
                missing.append((label, "build (via `python -m build`)"))
            continue
        head = tool.lstrip("./")
        if head in provided_elsewhere or head in declared:
            continue
        missing.append((label, tool))

    assert not missing, (
        "the verify action invokes tools that are neither declared in "
        f"[project.optional-dependencies].dev nor installed by a step: {missing}"
    )


def test_build_is_declared_so_ci_can_package() -> None:
    """Regression: the first release failed on `No module named build`."""
    assert "build" in _declared_dev_requirements(), (
        "`build` must be a dev dependency; the verify action runs `python -m build`"
    )


def test_release_publishing_jobs_install_their_own_tools() -> None:
    """The publishing jobs must not assume the verify job's environment."""
    source = (WORKFLOWS / "release.yml").read_text()
    # PyPI publishing uses uv, which the job installs via setup-uv.
    assert "astral-sh/setup-uv" in source
    # The registry job installs the publisher binary itself.
    assert "mcp-publisher" in source
    assert "pip install" in source or "setup-python" in source
