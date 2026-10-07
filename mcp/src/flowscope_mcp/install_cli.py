"""Console entry point for ``flowscope-mcp-install``.

Writes the right MCP client configuration for this machine, or prints it for a
client that has no conventional file. The generated shape differs per client
because the clients genuinely disagree; see :mod:`flowscope_mcp.install`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .install import (
    DEFAULT_API_URL,
    DEFAULT_PACKAGE_SPEC,
    TARGETS,
    describe_targets,
    merge_into_json_file,
    render_config,
    render_json_config,
    resolve_target_path,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flowscope-mcp-install",
        description=(
            "Write or print MCP client configuration for the FlowScope server. "
            "Merges into an existing JSON config, leaving other servers alone."
        ),
        epilog="Available clients:\n" + describe_targets(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "client",
        nargs="?",
        default="print",
        choices=sorted(TARGETS),
        help="Which client to configure. Defaults to 'print' (stdout only).",
    )
    parser.add_argument(
        "--print",
        dest="print_only",
        action="store_true",
        help="Print the configuration instead of writing it, even for a known client.",
    )
    parser.add_argument(
        "--api-url",
        default=None,
        help=(
            "FlowScope backend URL to write into the config. Omit to leave the "
            f"server's own default ({DEFAULT_API_URL}) in place."
        ),
    )
    parser.add_argument(
        "--python",
        default=None,
        help=(
            "Launch with this interpreter as `python -m flowscope_mcp` instead of "
            "uvx. Useful when the package is installed in a specific virtualenv."
        ),
    )
    parser.add_argument(
        "--package",
        default=DEFAULT_PACKAGE_SPEC,
        help=(
            "Distribution for uvx to run. Point it at a path or git URL to use a "
            "local checkout, e.g. --package /abs/path/to/flowscope/mcp"
        ),
    )
    parser.add_argument(
        "--project-dir",
        default=".",
        help="Directory that project-scoped config paths are relative to.",
    )
    parser.add_argument(
        "--version", action="version", version=f"flowscope-mcp-install {__version__}"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    target = TARGETS[args.client]

    render_kwargs = {
        "package_spec": args.package,
        "python": args.python,
        "api_url": args.api_url,
    }

    # Print-only clients, or an explicit --print, never touch the filesystem.
    if args.print_only or args.client == "print" or not target.config_path:
        print(render_config(args.client, **render_kwargs), end="")
        if target.config_path:
            print(f"\n# Intended location: {target.config_path}", file=sys.stderr)
        return 0

    path = resolve_target_path(target, project_dir=Path(args.project_dir).expanduser())
    assert path is not None  # guarded above

    if args.client == "codex":
        # TOML cannot be merged safely by text append if the table already
        # exists, so say so rather than producing a duplicate table.
        path.parent.mkdir(parents=True, exist_ok=True)
        already_present = path.exists() and "[mcp_servers.flowscope]" in path.read_text()
        with path.open("a") as handle:
            handle.write(("\n" if path.stat().st_size else "") + render_config("codex", **render_kwargs))
        if already_present:
            print(f"{path} already had a [mcp_servers.flowscope] table; removed the duplicate by hand.")
            return 1
        print(f"Appended the {target.label} server to {path}")
        return 0

    document = render_json_config(target, **render_kwargs)
    print(merge_into_json_file(path, target, document))
    for note in target.notes:
        print(f"  note: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
