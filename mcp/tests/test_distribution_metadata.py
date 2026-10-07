"""Tests for the distribution and plugin metadata.

These files are not executed by anything in this package, but a mistake in one
of them is invisible until a user's install fails. The constraints asserted here
are the ones the registries and clients actually enforce: the MCP Registry's
`server.json` schema limits, the Agent Skills frontmatter rules, and the plugin
manifest keys.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

try:  # tomllib is stdlib from Python 3.11; the package supports 3.10.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on the 3.10 job
    import tomli as tomllib

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parent

# Only these frontmatter fields are portable across Agent Skills clients.
# Anything else is a Claude Code extension and makes the skill unpublishable to
# claude.ai and rejected by the official `skills-ref` validator.
PORTABLE_SKILL_FIELDS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}


def _frontmatter(skill_file: Path) -> dict[str, str]:
    text = skill_file.read_text()
    assert text.startswith("---\n"), f"{skill_file} must open with YAML frontmatter"
    _, raw, _ = text.split("---\n", 2)
    fields: dict[str, str] = {}
    for line in raw.splitlines():
        if not line.strip() or line.startswith(" "):
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def _skill_dirs() -> list[Path]:
    return sorted(p for p in (PACKAGE_ROOT / "skills").iterdir() if p.is_dir())


def test_the_package_ships_at_least_one_skill() -> None:
    assert _skill_dirs(), "no skills found to publish"


def test_every_skill_directory_contains_a_skill_md() -> None:
    for skill in _skill_dirs():
        assert (skill / "SKILL.md").is_file(), f"{skill.name} has no SKILL.md"


def test_skill_frontmatter_uses_only_portable_fields() -> None:
    """A Claude-Code-only field makes the skill fail validation elsewhere."""
    for skill in _skill_dirs():
        fields = _frontmatter(skill / "SKILL.md")
        unexpected = set(fields) - PORTABLE_SKILL_FIELDS
        assert not unexpected, (
            f"{skill.name} uses non-portable frontmatter fields: {sorted(unexpected)}. "
            f"Only {sorted(PORTABLE_SKILL_FIELDS)} are part of the open Agent Skills spec."
        )


def test_skill_name_matches_its_directory() -> None:
    """The spec requires the name to equal the parent directory name."""
    for skill in _skill_dirs():
        fields = _frontmatter(skill / "SKILL.md")
        assert fields["name"] == skill.name, (
            f"{skill.name}/SKILL.md declares name: {fields['name']!r}; the spec requires "
            "these to match."
        )


def test_skill_names_are_valid_kebab_case() -> None:
    """1-64 chars, lowercase alphanumeric and single hyphens."""
    for skill in _skill_dirs():
        name = _frontmatter(skill / "SKILL.md")["name"]
        assert 1 <= len(name) <= 64, f"{name} is outside the 1-64 character limit"
        assert name == name.lower(), f"{name} must be lowercase"
        assert "--" not in name, f"{name} has consecutive hyphens"
        assert not name.startswith("-") and not name.endswith("-")
        assert all(c.isalnum() or c == "-" for c in name), f"{name} has invalid characters"


def test_skill_descriptions_fit_the_limit_and_include_triggers() -> None:
    """The description is what a client matches against, so it must state when to use the skill."""
    for skill in _skill_dirs():
        description = _frontmatter(skill / "SKILL.md")["description"]
        assert 1 <= len(description) <= 1024, f"{skill.name} description is {len(description)} chars"
        # Official guidance: state the trigger, not just the capability.
        assert "Use when" in description or "use when" in description, (
            f"{skill.name} should include an explicit 'Use when ...' trigger clause"
        )
        # Reject first/second person, which the authoring guidance calls out as
        # a cause of discovery problems.
        assert not description.startswith(("I ", "You ", "We ")), (
            f"{skill.name} should be third person"
        )


def test_skill_bodies_stay_within_the_recommended_size() -> None:
    """The official guidance is to keep SKILL.md under 500 lines."""
    for skill in _skill_dirs():
        lines = (skill / "SKILL.md").read_text().splitlines()
        assert len(lines) < 500, f"{skill.name}/SKILL.md is {len(lines)} lines"


def test_skill_cross_references_resolve_to_real_tools() -> None:
    """A skill that names a tool which does not exist sends the model nowhere."""
    import asyncio

    from flowscope_mcp.server import build_server

    server = build_server()
    tool_names = {tool.name for tool in asyncio.run(server.list_tools())}

    for skill in _skill_dirs():
        text = (skill / "SKILL.md").read_text()
        for candidate in text.replace("`", " ").split():
            if candidate.startswith("flowscope_"):
                cleaned = candidate.strip(".,;:()")
                assert cleaned in tool_names, (
                    f"{skill.name} references {cleaned!r}, which is not a registered tool"
                )


# ---- plugin manifests ------------------------------------------------------


def test_marketplace_manifest_has_the_required_keys() -> None:
    manifest = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    assert manifest["name"]
    assert manifest["owner"]["name"]
    plugins = manifest["plugins"]
    assert isinstance(plugins, list) and plugins
    for plugin in plugins:
        assert plugin["name"], "each plugin entry needs a name"
        assert plugin["source"], "each plugin entry needs a source"


def test_marketplace_plugin_source_exists() -> None:
    """A relative source resolves from the marketplace root, not from .claude-plugin/."""
    manifest = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    for plugin in manifest["plugins"]:
        source = plugin["source"]
        if isinstance(source, str) and source.startswith("./"):
            resolved = (REPO_ROOT / source).resolve()
            assert resolved.is_dir(), f"plugin source {source} does not exist at {resolved}"


def test_plugin_manifest_is_named_and_kept_minimal() -> None:
    """`name` is the only required key; plugin.json is optional but useful."""
    manifest = json.loads((PACKAGE_ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "flowscope"


def test_bundled_mcp_configuration_uses_the_mcpservers_wrapper() -> None:
    """The wrapper key is required; a bare server map is silently ignored."""
    config = json.loads((PACKAGE_ROOT / ".mcp.json").read_text())
    assert "mcpServers" in config, "the plugin .mcp.json must nest servers under 'mcpServers'"
    assert "flowscope" in config["mcpServers"]
    entry = config["mcpServers"]["flowscope"]
    assert entry["command"] == "uvx"
    # Resolves to the plugin root when the host provides it.
    assert any("CLAUDE_PLUGIN_ROOT" in str(arg) for arg in entry["args"]), (
        "the bundled config should locate the server relative to the plugin root"
    )


def test_pyproject_declares_both_console_scripts() -> None:
    """`uvx <dist>` needs the script name to match the distribution name."""
    text = (PACKAGE_ROOT / "pyproject.toml").read_text()
    assert 'flowscope-mcp = "flowscope_mcp.__main__:main"' in text
    assert 'flowscope-mcp-install = "flowscope_mcp.install_cli:main"' in text
    # uvx needs the extra only for the `mcp` CLI, which we do not require.
    assert "requires-python" in text


# ---- MCP registry metadata -------------------------------------------------


@pytest.fixture
def server_json() -> dict:
    return json.loads((PACKAGE_ROOT / "server.json").read_text())


def test_server_json_satisfies_the_registry_constraints(server_json: dict) -> None:
    """These limits come from the official server.schema.json; violating them
    makes `mcp-publisher publish` fail with a 422."""
    name = server_json["name"]
    assert 3 <= len(name) <= 200
    # Reverse-DNS with exactly one forward slash.
    assert name.count("/") == 1, "name must be reverse-DNS with exactly one slash"
    namespace, server_part = name.split("/")
    assert namespace and server_part
    assert all(c.isalnum() or c in ".-" for c in namespace)
    assert all(c.isalnum() or c in "._-" for c in server_part)

    # The description limit is 100 characters, which is easy to exceed.
    assert 1 <= len(server_json["description"]) <= 100, (
        f"description is {len(server_json['description'])} chars; the registry limit is 100"
    )
    assert server_json["version"]
    assert not any(c in server_json["version"] for c in "^~*"), "version ranges are rejected"


def test_server_json_declares_an_installable_package(server_json: dict) -> None:
    """A server with no `packages` and no `remotes` cannot be installed."""
    packages = server_json.get("packages") or []
    assert packages, "a server needs at least one package or remote to be installable"
    for package in packages:
        assert package["registryType"] in {"npm", "pypi", "cargo", "oci", "nuget", "mcpb"}
        assert package["identifier"]
        assert package["transport"]["type"] in {"stdio", "streamable-http", "sse"}
        # `runtimeHint` is what tells a client to reach for uvx.
        assert package["runtimeHint"] == "uvx"
        # The version must be exact, never a range.
        assert package["version"] == server_json["version"]


def test_server_json_version_matches_the_package(server_json: dict) -> None:
    """The registry version should track the distribution version."""
    from flowscope_mcp import __version__

    assert server_json["version"] == __version__


def test_pypi_ownership_marker_is_in_the_readme(server_json: dict) -> None:
    """The registry verifies PyPI ownership by finding this exact string."""
    readme = (PACKAGE_ROOT / "README.md").read_text()
    assert f"mcp-name: {server_json['name']}" in readme, (
        "README.md must contain 'mcp-name: <server.json name>' for the registry to "
        "verify that this project owns the PyPI package"
    )


def test_server_json_meta_stays_under_the_size_limit(server_json: dict) -> None:
    """Publisher-provided metadata over 4 KB makes publishing fail."""
    encoded = json.dumps(server_json.get("_meta", {})).encode()
    assert len(encoded) < 4096, f"_meta is {len(encoded)} bytes; the limit is 4096"


def test_declared_environment_variables_match_what_the_server_reads(server_json: dict) -> None:
    """A documented variable the code never reads is a support burden."""
    from flowscope_mcp import settings as settings_module

    source = Path(settings_module.__file__).read_text()
    package = server_json["packages"][0]
    declared = {var["name"] for var in package.get("environmentVariables", [])}
    assert declared, "the base URL should be declared so clients can prompt for it"
    for name in declared:
        assert name in source, f"server.json documents {name}, which settings.py does not read"

    # And the reverse: nothing the server reads should be undocumented, apart
    # from tuning knobs that a client has no business setting.
    documented_or_internal = declared | {
        "FLOWSCOPE_POLL_INTERVAL",
        "FLOWSCOPE_HTTP_TIMEOUT",
    }
    import re

    read_names = set(re.findall(r'os\.environ\.get\(\s*"(FLOWSCOPE_[A-Z_]+)"', source))
    assert read_names <= documented_or_internal, (
        f"undocumented environment variables: {sorted(read_names - documented_or_internal)}"
    )


# ---- installation docs must match the code ---------------------------------
#
# Install instructions rot silently: they are not imported and never executed.
# These tests tie the documented client list and skill directories back to the
# single definitions in install.py and skills/, so a change there fails here
# rather than in a user's terminal.


def _doc(name: str) -> str:
    return (PACKAGE_ROOT / "docs" / name).read_text()


def test_every_installer_target_is_documented() -> None:
    """A client the installer supports but the docs omit is undiscoverable."""
    from flowscope_mcp.install import TARGETS

    combined = _doc("install.md") + (PACKAGE_ROOT / "README.md").read_text()
    for key in TARGETS:
        if key == "print":
            continue
        assert key in combined, (
            f"install.py supports the {key!r} target but neither README.md nor "
            "docs/install.md mentions it"
        )


def test_documented_client_names_resolve_to_real_targets() -> None:
    """A documented command that is not a valid target would fail on use."""
    from flowscope_mcp.install import TARGETS

    for key in TARGETS:
        # The generator's own help output is what a user copies from.
        from flowscope_mcp.install import render_config

        assert render_config(key).strip(), f"{key} renders nothing"


def test_install_docs_mention_the_published_package() -> None:
    """The PyPI route is the primary install path and must be documented."""
    text = _doc("install.md") + (PACKAGE_ROOT / "README.md").read_text()
    assert "uvx flowscope-mcp" in text, "the published uvx invocation is not documented"
    assert "uvx flowscope-mcp@0.1.0" in text or "flowscope-mcp@" in text, (
        "pinning a published version should be documented"
    )


def test_install_docs_cover_a_remote_transport() -> None:
    """Harnesses that cannot spawn a process need the HTTP route."""
    text = _doc("install.md")
    assert "streamable-http" in text
    assert "/mcp" in text


def test_skills_docs_list_the_verified_client_directories() -> None:
    """These paths come from each client's documentation; a typo breaks installs."""
    text = _doc("skills.md")
    for path in (
        ".claude/skills/",
        "~/.codex/skills/",
        ".cursor/skills/",
        ".github/skills/",
        "~/.gemini/skills/",
        ".agents/skills/",
    ):
        assert path in text, f"docs/skills.md omits the {path} location"


def test_every_bundled_skill_is_documented() -> None:
    """Adding a skill without documenting it leaves it uninstallable."""
    for skill in _skill_dirs():
        assert skill.name in _doc("skills.md"), f"docs/skills.md does not mention {skill.name}"
        assert skill.name in (PACKAGE_ROOT / "README.md").read_text(), (
            f"README.md does not mention the {skill.name} skill"
        )


def test_docs_agree_with_the_actual_skill_directory_count() -> None:
    """A count in prose is a claim; keep it honest against the filesystem."""
    import re

    readme = (PACKAGE_ROOT / "README.md").read_text()
    match = re.search(r"^(\w+) \[Agent Skills\]", readme, re.MULTILINE)
    assert match, "could not find the skills introduction in README.md"
    words = {"two": 2, "three": 3, "four": 4}
    if match.group(1).lower() in words:
        assert words[match.group(1).lower()] == len(_skill_dirs()), (
            f"README.md says {match.group(1)!r} skills but {len(_skill_dirs())} exist"
        )


def test_docs_have_no_broken_relative_links() -> None:
    """A dead link in an install guide is worse than no link."""
    import re

    for source in [PACKAGE_ROOT / "README.md"] + sorted((PACKAGE_ROOT / "docs").glob("*.md")):
        text = source.read_text()
        for target in re.findall(r"\]\(([^)]+)\)", text):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            # Strip any anchor; only the file needs to exist.
            path = target.split("#", 1)[0]
            if not path:
                continue
            resolved = (source.parent / path).resolve()
            assert resolved.exists(), f"{source.relative_to(PACKAGE_ROOT)} links to missing {target}"


def test_readme_documents_the_plugin_route() -> None:
    """The one-step route is the least discoverable and the most convenient."""
    readme = (PACKAGE_ROOT / "README.md").read_text()
    assert "/plugin marketplace add" in readme
    assert "/plugin install" in readme


def test_uvx_invocations_name_a_real_distribution() -> None:
    """`uvx <cmd>` installs the package *named* `<cmd>`.

    `flowscope-mcp-install` is a console script, not a distribution, so it must
    always be reached through `--from flowscope-mcp`. Writing the bare form sends
    users to a package that does not exist.
    """
    import re

    docs = [REPO_ROOT / "README.md", PACKAGE_ROOT / "README.md"] + sorted(
        (PACKAGE_ROOT / "docs").glob("*.md")
    )
    bad = re.compile(r"uvx\s+flowscope-mcp-install")
    for doc in docs:
        for lineno, line in enumerate(doc.read_text().splitlines(), 1):
            assert not bad.search(line), (
                f"{doc.relative_to(REPO_ROOT)}:{lineno} runs `uvx flowscope-mcp-install`, "
                "but that is a console script inside the `flowscope-mcp` distribution. "
                "Use `uvx --from flowscope-mcp flowscope-mcp-install`."
            )


def test_uvx_invocations_use_the_real_distribution_name() -> None:
    """The distribution is `flowscope-mcp`; the same name provides `flowscope-mcp`."""
    text = (PACKAGE_ROOT / "README.md").read_text()
    assert "uvx flowscope-mcp" in text, "the bare `uvx flowscope-mcp` route should be documented"

    # And the console-script name must match the distribution so that bare form works.

    pyproject = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text())
    project = pyproject["project"]
    scripts = project["scripts"]
    assert project["name"] in scripts, (
        f"distribution {project['name']!r} does not expose a script of the same name, "
        "so `uvx <dist>` would not resolve"
    )


def test_documented_tool_names_all_exist() -> None:
    """A doc that names a tool which does not exist sends the model nowhere.

    The skills are checked separately; this covers the prose docs, where a typo
    is just as damaging and much easier to make.
    """
    import asyncio
    import re

    from flowscope_mcp.server import build_server

    server = build_server()
    # Prompts share the `flowscope_` prefix, so both namespaces are legitimate.
    real = {tool.name for tool in asyncio.run(server.list_tools())}
    real |= {prompt.name for prompt in asyncio.run(server.list_prompts())}

    docs = [PACKAGE_ROOT / "README.md"] + sorted((PACKAGE_ROOT / "docs").glob("*.md"))
    pattern = re.compile(r"\bflowscope_[a-z_]+\b")
    # `flowscope_mcp` is the importable module, not a tool; `python -m flowscope_mcp`
    # legitimately appears in install instructions.
    not_a_tool = {"flowscope_mcp"}
    for doc in docs:
        text = doc.read_text()
        for match in sorted(set(pattern.findall(text)) - not_a_tool):
            assert match in real, (
                f"{doc.relative_to(PACKAGE_ROOT)} references {match!r}, which is neither a "
                f"registered tool nor a prompt. Known: {sorted(real)}"
            )


def test_prompt_names_in_docs_exist() -> None:
    """Slash-command and prompt names are easy to typo and hard to notice."""
    import asyncio
    import re

    from flowscope_mcp.server import build_server

    server = build_server()
    real = {prompt.name for prompt in asyncio.run(server.list_prompts())}

    readme = (PACKAGE_ROOT / "README.md").read_text()
    for name in real:
        # The README should mention each prompt, so users know it exists.
        assert name in readme, f"README.md does not mention the {name} prompt"

    # Any slash-command-looking token in the docs must be a real prompt.
    for doc in [PACKAGE_ROOT / "README.md", PACKAGE_ROOT / "docs" / "usage.md"]:
        for candidate in set(re.findall(r"/(flowscope_[a-z_]+)", doc.read_text())):
            assert candidate in real, f"{doc.name} shows /{candidate}, which is not a prompt"


def test_usage_doc_covers_every_costly_tool() -> None:
    """The doc that explains cost and recovery should name the tools involved."""
    text = (PACKAGE_ROOT / "docs" / "usage.md").read_text()
    for tool in (
        "flowscope_health_check",
        "flowscope_list_videos",
        "flowscope_analyze_video",
        "flowscope_job_status",
        "flowscope_get_report",
        "flowscope_retry_video",
        "flowscope_frame_image",
    ):
        assert tool in text, f"docs/usage.md never mentions {tool}"


def test_github_namespace_casing_matches_the_real_account(server_json: dict) -> None:
    """The `io.github.*` owner must match the GitHub account's exact casing.

    The registry creates a permission pattern from the OIDC token's
    `repository_owner` claim and compares it with a case-sensitive prefix match:

        You have permission to publish: io.github.DavidNgugi/*
        Attempting to publish: io.github.davidngugi/flowscope     -> 403

    GitHub logins are case-insensitive, so lowercase looks correct and fails at
    publish time. See modelcontextprotocol/registry#689.

    If this ever fails because the account casing changed, update the namespace
    here, in server.json, and the `mcp-name:` marker in README.md together.
    """
    name = server_json["name"]
    namespace = name.split("/")[0]

    if namespace.startswith("io.github."):
        owner = namespace[len("io.github.") :]
        # A lowercase-only owner is almost certainly the bug above: the
        # authenticated account is necessarily mixed case or the check would not
        # be interesting, and the two real-world owners in this repo are mixed.
        assert owner != owner.lower(), (
            f"the io.github namespace owner {owner!r} is all lowercase. The registry "
            "matches it case-sensitively against the GitHub account, so it must use the "
            "account's canonical casing. See registry issue #689."
        )
        assert owner[0].isupper(), f"{owner!r} should start with the account's real capital"


def test_the_readme_marker_matches_the_server_name_exactly(server_json: dict) -> None:
    """The marker is matched as a literal string, so case matters here too."""
    readme = (PACKAGE_ROOT / "README.md").read_text()
    assert f"mcp-name: {server_json['name']}" in readme


def test_repository_urls_use_the_same_owner_casing(server_json: dict) -> None:
    """A half-renamed repo is confusing; keep every URL on one spelling."""
    owner = server_json["name"].split("/")[0].removeprefix("io.github.")
    repository = server_json["repository"]["url"]
    assert f"github.com/{owner}/" in repository, (
        f"repository URL {repository!r} does not use the namespace owner casing {owner!r}"
    )
    for doc in [PACKAGE_ROOT / "README.md", PACKAGE_ROOT / "pyproject.toml"]:
        text = doc.read_text()
        if "github.com/" in text:
            assert f"github.com/{owner.lower()}/" not in text, (
                f"{doc.name} contains a lowercase GitHub URL while the namespace uses {owner!r}"
            )
