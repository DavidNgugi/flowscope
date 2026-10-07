"""Console entry point for the FlowScope MCP server.

MCP clients invoke a stdio server as a plain command, so the no-argument
default must be ``stdio`` and must not print anything to stdout that is not a
protocol frame. Diagnostics therefore go to stderr, which clients surface in
their own logs.

The HTTP transports are exposed for the case where one long-lived server is
shared by several clients, which is how a hosted deployment would run.
"""

from __future__ import annotations

import argparse
import sys

from . import __version__


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flowscope-mcp",
        description=(
            "MCP server exposing FlowScope's YouTube UX-analysis pipeline to any "
            "Model Context Protocol client."
        ),
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
        help="Transport to serve on. stdio (default) is what MCP clients expect.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host for HTTP transports.")
    parser.add_argument("--port", type=int, default=8765, help="Bind port for HTTP transports.")
    parser.add_argument(
        "--version",
        action="version",
        version=f"flowscope-mcp {__version__}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    # Imported lazily so `--version` and `--help` stay fast and cannot fail on
    # a partially installed dependency set.
    from .server import build_server

    server = build_server()

    if args.transport == "stdio":
        print(
            f"flowscope-mcp {__version__}: serving over stdio. "
            "Set FLOWSCOPE_API_URL if the backend is not on http://127.0.0.1:8000.",
            file=sys.stderr,
        )
        server.run(transport="stdio")
    elif args.transport == "streamable-http":
        print(
            f"flowscope-mcp {__version__}: serving Streamable HTTP on "
            f"http://{args.host}:{args.port}/mcp",
            file=sys.stderr,
        )
        server.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        print(
            f"flowscope-mcp {__version__}: serving the deprecated HTTP+SSE transport on "
            f"http://{args.host}:{args.port}/sse",
            file=sys.stderr,
        )
        server.run(transport="sse", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
