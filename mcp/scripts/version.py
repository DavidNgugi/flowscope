#!/usr/bin/env python3
"""Read, check, and bump the version that several artifacts must agree on.

A release touches more files than a normal project: the Python distribution, the
runtime ``__version__``, the MCP Registry record (twice — the server and the
package inside it), the plugin manifest, and the marketplace manifest. If any
one of them disagrees, the failure surfaces *during* publishing — after PyPI has
already accepted the upload and the version number is burned, because PyPI never
lets you reuse one.

So the version lives in ``mcp/pyproject.toml`` as the single source of truth, and
this script propagates and verifies it.

    python scripts/version.py check              # verify everything agrees
    python scripts/version.py bump 0.2.0         # set every site, then show diff
    python scripts/version.py show

Every rewrite is done with an anchored substitution, so a version string inside
prose or an unrelated field cannot be clobbered by accident.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_OF_TRUTH = REPO_ROOT / "mcp" / "pyproject.toml"

# A version is digits and dots, optionally followed by a `-` or `+` suffix:
# 1.2.3, 0.1.0-rc1, 1.0.0-alpha.2, 2.0.0+build.5
#
# PEP 440's separator-less forms (`1.0.0a1`, `1.0.0.post1`) are deliberately NOT
# accepted. Requiring an explicit separator keeps the pattern unambiguous, and
# a hyphenated prerelease (`1.0.0-rc1`) is what the MCP Registry, PyPI, and git
# tags all handle identically.
VERSION_PATTERN = r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?"

# Used to validate a version. One pattern, so validation here can never disagree
# with what the bump command or the workflow accepts.
VALID_VERSION = re.compile(rf"^{VERSION_PATTERN}$")


def is_prerelease(candidate: str) -> bool:
    """True for a hyphenated suffix (`0.2.0-rc1`), False for build metadata.

    A `+` suffix is build metadata, which does not make a release a prerelease;
    a `-` suffix does.
    """
    return bool(re.match(r"^\d+\.\d+\.\d+-", candidate))


@dataclass(frozen=True)
class VersionSite:
    """One place a version is declared, and how to read or rewrite it."""

    label: str
    path: Path
    #: Anchored on the key, so only the intended value can match.
    pattern: str

    @property
    def display_path(self) -> str:
        try:
            return str(self.path.relative_to(REPO_ROOT))
        except ValueError:
            return str(self.path)

    def read(self) -> str | None:
        match = re.search(self.pattern, self.path.read_text(), re.MULTILINE)
        return match.group("version") if match else None

    def write(self, version: str) -> bool:
        """Set the version. Returns True when the file actually changed."""
        text = self.path.read_text()
        # MULTILINE is required: every pattern is anchored with `^`, and the
        # version is never on the first line of the file.
        replaced, count = re.subn(
            self.pattern,
            lambda m: m.group(0).replace(m.group("version"), version),
            text,
            flags=re.MULTILINE,
        )
        if count == 0:
            raise SystemExit(f"error: no version found in {self.display_path}")
        if replaced != text:
            self.path.write_text(replaced)
            return True
        return False


def version_sites() -> list[VersionSite]:
    return [
        VersionSite(
            "pyproject.toml (source of truth)",
            SOURCE_OF_TRUTH,
            r'^version = "(?P<version>' + VERSION_PATTERN + r')"',
        ),
        VersionSite(
            "__init__.py (runtime)",
            REPO_ROOT / "mcp" / "src" / "flowscope_mcp" / "__init__.py",
            r'^__version__ = "(?P<version>' + VERSION_PATTERN + r')"',
        ),
        # server.json declares the version twice: once for the server record and
        # once for the package inside it. Both must match. The two patterns are
        # distinguished by the key that precedes them.
        VersionSite(
            "server.json (server)",
            REPO_ROOT / "mcp" / "server.json",
            r'^  "version": "(?P<version>' + VERSION_PATTERN + r')",',
        ),
        VersionSite(
            "server.json (package)",
            REPO_ROOT / "mcp" / "server.json",
            r'^      "version": "(?P<version>' + VERSION_PATTERN + r')",?$',
        ),
        VersionSite(
            "plugin.json",
            REPO_ROOT / "mcp" / ".claude-plugin" / "plugin.json",
            r'^  "version": "(?P<version>' + VERSION_PATTERN + r')",',
        ),
        VersionSite(
            "marketplace.json",
            REPO_ROOT / ".claude-plugin" / "marketplace.json",
            r'^  "version": "(?P<version>' + VERSION_PATTERN + r')",',
        ),
    ]


def collect() -> list[tuple[VersionSite, str | None]]:
    return [(site, site.read()) for site in version_sites()]


def source_version() -> str:
    site = version_sites()[0]
    value = site.read()
    if value is None:
        raise SystemExit(f"error: no version in {site.display_path}")
    return value


def command_show() -> int:
    for site, value in collect():
        print(f"  {site.label:34s} {value}")
    return 0


def command_check() -> int:
    """Fail unless every declared version matches, and the tag matches too."""
    found = collect()
    canonical = found[0][1]
    problems: list[str] = []

    if not canonical or not VALID_VERSION.match(canonical):
        problems.append(f"{found[0][0].display_path} has an invalid version: {canonical!r}")

    for site, value in found:
        if value != canonical:
            problems.append(
                f"{site.display_path} ({site.label}) is {value!r}, expected {canonical!r}"
            )

    # When running on a tag, the tag must agree with the package version.
    tag = _git_tag()
    if tag:
        expected = tag.lstrip("v")
        if expected != canonical:
            problems.append(
                f"git tag {tag!r} implies version {expected!r}, but the package declares {canonical!r}"
            )

    if problems:
        for problem in problems:
            print(f"::error::{problem}")
        print("\nRun `python scripts/version.py bump <version>` to set them all at once.")
        return 1

    print(f"versions agree: {canonical}")
    return 0


def _git_tag() -> str | None:
    """The exact tag pointing at HEAD, if any (works on GitHub Actions)."""
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--exact-match", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def command_bump(new_version: str) -> int:
    if not VALID_VERSION.match(new_version):
        raise SystemExit(
            f"error: {new_version!r} is not a plain version. "
            "Version ranges are rejected by the MCP Registry, so use e.g. 0.2.0 or 0.2.0-rc1."
        )

    current = source_version()
    if new_version == current:
        print(f"already at {new_version}; nothing to do")
        return 0

    changed: list[str] = []
    for site in version_sites():
        if site.write(new_version):
            changed.append(site.display_path)

    print(f"{current} -> {new_version}")
    for path in changed:
        print(f"  updated {path}")

    # Point the operator at the remaining manual steps rather than doing them,
    # because staging and committing are decisions, not side effects.
    print(
        "\nNext:\n"
        f"  git add -A && git commit -m 'release: v{new_version}'\n"
        f"  git tag -a v{new_version} -m 'v{new_version}'\n"
        "  git push && git push --tags\n"
        "\nPushing the tag runs the release workflow."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show", help="Print every declared version.")
    sub.add_parser(
        "prerelease",
        help=(
            "Print 'true' if the current version is a prerelease, else 'false'. "
            "Used by the release workflow so the classification has one definition."
        ),
    )
    sub.add_parser("check", help="Verify every declared version agrees, including the git tag.")
    bump = sub.add_parser("bump", help="Set every declared version.")
    bump.add_argument("version", help="The new version, e.g. 0.2.0 or 0.2.0-rc1")

    args = parser.parse_args(argv)
    if args.command == "show":
        return command_show()
    if args.command == "check":
        return command_check()
    if args.command == "prerelease":
        print("true" if is_prerelease(source_version()) else "false")
        return 0
    return command_bump(args.version)


if __name__ == "__main__":
    raise SystemExit(main())
